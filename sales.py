"""チケット発売情報（1回券・定期会員券）とチケット関連ニュースを集める。scraper.py から呼ばれる。

- N響: 1回券／シーズン会員券／年間会員券ページの「発売開始日」
- 都響・東響: 各公演ページの発売日欄（同じ発売日の公演はまとめる）
- ニュース: 各楽団のお知らせのうちチケット関連のもの
"""
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

from scraper import TODAY, WEEKDAYS, clean, get

CACHE = Path(__file__).parent / "data" / "sales_cache.json"
CACHE_DAYS = 7  # 公演ページの発売日は頻繁には変わらないので、1週間は取り直さない

DATE_RE = re.compile(
    r"(?:(?P<y>20\d{2})\s*[年/.]\s*)?(?P<m>\d{1,2})\s*[月/]\s*(?P<d>\d{1,2})\s*日?"
    r"\s*(?:[（(][^）)]*[）)])?"
    r"\s*(?:(?P<h>\d{1,2})\s*[:：時]\s*(?P<mi>\d{2})?\s*(?P<ap>am|pm)?)?",
    re.I,
)


def parse_sale_lines(lines, ref=None):
    """発売日欄の行から [{group, label, date, time}] を作る。

    行の例:「■定期会員券」「東響会員先行発売…2026年11/25（水）10:00」「一般：2026年7月26日（日）10:00am」
    年がない行は直前の年を引き継ぐ（月が戻ったら翌年）。最初から年がなければ ref（公演日）以前で最も近い日付とみなす。
    """
    events, group, year, last_month = [], "", None, None
    for line in lines:
        m = DATE_RE.search(line)
        label = clean(re.sub(r"[■□◇◆●・…：:\s]+$", "", line[:m.start()]).lstrip("■□◇◆●・ ")) if m else ""
        if not m or not label:
            # 日付のない行は見出し（券種や対象公演）
            if not m:
                group = heading(line)
            continue
        mo, d = int(m["m"]), int(m["d"])
        if m["y"]:
            year = int(m["y"])
        elif year is not None:
            if last_month and mo < last_month:
                year += 1
        else:
            base = date.fromisoformat(ref) if ref else TODAY
            year = base.year if (mo, d) <= (base.month, base.day) else base.year - 1
        last_month = mo
        try:
            dt = date(year, mo, d)
        except ValueError:  # 「2026/27シーズン」のような見出し
            group = heading(line)
            continue
        t = ""
        if m["h"]:
            h = int(m["h"])
            if (m["ap"] or "").lower() == "pm" and h < 12:
                h += 12
            t = f"{h:02d}:{m['mi'] or '00'}"
        events.append({"group": group, "label": label, "date": dt.isoformat(), "time": t})
    return events


def heading(line):
    return clean(line.strip("■□◇◆●［］[] "))


def block(text, start, stops):
    """ページ本文から start 以降、stops のいずれかが出るまでの行を取り出す。

    start が複数回出る場合（メニューにも「チケット」がある等）は、日付を含む最初のものを使う。
    """
    for m in re.finditer(re.escape(start), text):
        lines = []
        for line in text[m.end():].split("\n"):
            line = clean(line)
            if any(line.startswith(s) for s in stops) or len(lines) >= 30:
                break
            if line:
                lines.append(line)
        if any(DATE_RE.search(l) and re.search(r"\d+\s*[月/]\s*\d+", l) for l in lines):
            return lines
    return []


def page_text(url):
    r = get(url)
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser").get_text("\n", strip=True)


# ---------------------------------------------------------------- N響
NHK_PAGES = [
    ("1回券", "https://www.nhkso.or.jp/ticket/single/index.html"),
    ("シーズン会員券", "https://www.nhkso.or.jp/ticket/subscription/season/index.html"),
    ("年間会員券", "https://www.nhkso.or.jp/ticket/subscription/annual/index.html"),
]


def nhk_sales():
    sales = []
    for kind, url in NHK_PAGES:
        text = page_text(url)
        lines = block(text, "発売開始日", ["※", "お申し込み", "N響主催公演", "その他のチケット"])
        for e in parse_sale_lines(lines):
            sales.append(sale("N響", e, f"{kind}（{e['group']}）" if e["group"] else kind, kind, url))
    return sales


SEASON_MONTHS = {"AUTUMN": {9, 10, 11}, "WINTER": {12, 1, 2}, "SPRING": {4, 5, 6}}


def link_nhk(sales, concerts):
    """N響の発売情報（「12・1・2月公演」「WINTERシーズン」など）に、対象の定期公演を紐付ける。"""
    subs = [c for c in concerts if c["orchestra"] == "N響" and c["series"].startswith("定期公演")]
    for s in sales:
        if s["orchestra"] != "N響":
            continue
        g = s["target"]
        m = re.search(r"([\d・]+)月公演", g)
        months = {int(x) for x in m[1].split("・") if x} if m else \
            next((v for k, v in SEASON_MONTHS.items() if k in g.upper()), None)
        if months is None and "年間" not in g:
            continue
        limit = (date.fromisoformat(s["date"]) + timedelta(days=366)).isoformat()
        s["concerts"] = [c["key"] for c in subs if s["date"] < c["date"] < limit
                         and (months is None or int(c["date"][5:7]) in months)]


# ---------------------------------------------------------------- 都響・東響（公演ページ）
def concert_sale_lines(c, text):
    if c["orchestra"] == "都響":
        return block(text, "\nチケット\n", ["チケット購入", "※", "チケット料金"])
    return block(text, "\n発売日\n", ["主催等", "TOKYO SYMPHONY", "※"])


def concert_sales(concerts):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    fresh = (datetime.now() - timedelta(days=CACHE_DAYS)).isoformat()
    targets = [c for c in concerts if c["orchestra"] in ("都響", "東響")
               and re.match(r"https://(www\.tmso\.or\.jp|tokyosymphony\.jp)/", c["url"])]
    todo = [c for c in targets if cache.get(c["url"], {}).get("fetched", "") < fresh]
    print(f"  公演ページ {len(todo)} 件を確認（{len(targets) - len(todo)} 件はキャッシュ）", flush=True)
    for c in todo:
        try:
            text = "\n" + page_text(c["url"])
        except Exception as e:
            print(f"  失敗 {c['url']}: {e}")
            continue
        # 解析ルールを直したときにすぐ反映できるよう、解析前の行を保存しておく
        cache[c["url"]] = {"fetched": datetime.now().isoformat(timespec="minutes"),
                           "lines": concert_sale_lines(c, text)}
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))

    # 同じ「楽団・発売日・区分・券種」の公演を1件にまとめる
    merged = {}
    for c in targets:
        for e in parse_sale_lines(cache.get(c["url"], {}).get("lines", []), ref=c["date"]):
            kind = e["group"] or "1回券"
            k = (c["orchestra"], e["date"], e["time"], e["label"], kind)
            if k not in merged:
                merged[k] = sale(c["orchestra"], e, kind, kind, c["url"])
            merged[k]["concerts"].append(c["key"])
    return list(merged.values())


def sale(orch, e, target, kind, url):
    d = date.fromisoformat(e["date"])
    subscription = bool(re.search(r"会員券|セット券|年間|シーズン", kind))
    single = "1回券" in kind or not subscription
    return {
        "orchestra": orch,
        "date": e["date"],
        "weekday": WEEKDAYS[d.weekday()],
        "time": e["time"],
        "label": e["label"],          # 一般発売 / 会員先行 など
        "target": target,             # 何が発売されるか
        "type": "・".join(t for t, on in [("定期会員券", subscription), ("1回券", single)] if on),
        "url": url,
        "concerts": [],
    }


# ---------------------------------------------------------------- ニュース
NEWS_RE = re.compile(r"発売|チケット|会員券|先行|販売|追加席")
NEWS_EXCLUDE = re.compile(r"CD|ディスク|写真集|書籍|香水|グッズ|Tシャツ|お詫び|不具合")


def nhk_news():
    items = get("https://www.nhkso.or.jp/news/json/news_all.json").json()["news"]
    return [{"orchestra": "N響", "date": n["date"], "title": clean(re.sub(r"<[^>]+>", " ", n["title"])),
             "url": "https://www.nhkso.or.jp" + n["url"]} for n in items]


def tmso_news():
    r = get("https://www.tmso.or.jp/j/news/")
    r.encoding = "utf-8"
    out = []
    for li in BeautifulSoup(r.text, "html.parser").select("li"):
        t, a = li.select_one("time.news-date"), li.select_one("dd a[href]")
        if t and a:
            out.append({"orchestra": "都響", "date": clean(t.get_text()).replace(".", "-"),
                        "title": clean(a.get_text()), "url": a["href"]})
    return out


def tso_news():
    r = get("https://tokyosymphony.jp/news/")
    r.encoding = "utf-8"
    out = []
    for a in BeautifulSoup(r.text, "html.parser").select("a.information__link"):
        t, title = a.select_one("time"), a.select_one(".information__link-title")
        if t and title:
            out.append({"orchestra": "東響", "date": clean(t.get_text()).replace(".", "-"),
                        "title": clean(title.get_text()), "url": a["href"]})
    return out


def ticket_news():
    since = (TODAY - timedelta(days=90)).isoformat()
    news, errors = [], {}
    for name, fn in [("N響ニュース", nhk_news), ("都響ニュース", tmso_news), ("東響ニュース", tso_news)]:
        try:
            news += [n for n in fn() if n["date"] >= since and NEWS_RE.search(n["title"])
                     and not NEWS_EXCLUDE.search(n["title"])]
        except Exception as e:
            errors[name] = str(e)
    news.sort(key=lambda n: n["date"], reverse=True)
    return news, errors


# ---------------------------------------------------------------- まとめ
def collect(concerts):
    sales, errors = [], {}
    for name, fn in [("N響発売日", nhk_sales), ("都響・東響発売日", lambda: concert_sales(concerts))]:
        try:
            sales += fn()
        except Exception as e:
            errors[name] = str(e)
    link_nhk(sales, concerts)
    news, news_errors = ticket_news()
    errors.update(news_errors)
    sales.sort(key=lambda s: (s["date"], s["time"], s["orchestra"]))
    return sales, news, errors
