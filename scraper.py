"""N響・都響・東響の公演情報と、おけぴ掲示板の譲渡チケットを集めて data/concerts.json に保存する。

使い方: python3 scraper.py
"""
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
OUT = Path(__file__).parent / "data" / "concerts.json"
TODAY = date.today()
WEEKDAYS = "月火水木金土日"

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})


def get(url, **kw):
    """サーバーに負担をかけないよう、リクエストごとに少し待つ。"""
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30, **kw)
            r.raise_for_status()
            time.sleep(1.0)
            return r
        except requests.RequestException as e:
            if attempt == 2:
                raise
            print(f"  retry {url}: {e}", file=sys.stderr)
            time.sleep(3)


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def lines_of(el):
    """<br> 区切りのテキストを行のリストにする。"""
    if el is None:
        return []
    return [clean(t) for t in el.get_text("\n").split("\n") if clean(t)]


def concert(orch, d, time_, title, hall, performers, program, url, series=""):
    return {
        "orchestra": orch,
        "date": d.isoformat(),
        "weekday": WEEKDAYS[d.weekday()],
        "time": time_,
        "title": title,
        "series": series,
        "hall": hall,
        "performers": performers,
        "program": program,
        "url": url,
    }


# ---------------------------------------------------------------- N響
def scrape_nhk():
    base = "https://www.nhkso.or.jp"
    halls = get(base + "/concert/hall/json/hall_all.json").json()["hall_info"]
    years = get(base + "/concert/json/list_archive_concert.json").json()["year_list"]
    series_names = {"a": "定期公演 Aプログラム", "b": "定期公演 Bプログラム", "c": "定期公演 Cプログラム"}
    category_names = {"special": "特別公演", "other": "各地の公演"}

    results = []
    # 直近2年分のファイルだけ見れば今後の公演は網羅できる
    for path in years[-2:]:
        for c in get(base + path).json()["concerts"]:
            hall = halls.get(c.get("hall_id", ""), {}).get("name", "")
            performers = [clean(p["text"]).replace(" : ", "：") for p in c.get("main_performers", [])]
            program = [clean(s["name"]) for s in c.get("songs", []) if clean(s["name"])]
            series = series_names.get(c.get("program", ""), "") if c["category"] == "subscription" \
                else category_names.get(c["category"], "")
            for det in c.get("details", []):
                d = date.fromisoformat(det["date"])
                if d < TODAY:
                    continue
                results.append(concert(
                    "N響", d, nhk_time(det.get("start_time", "")), clean(c["title"]), clean(hall),
                    performers, program, urljoin(base, c["details_url"]), series,
                ))
    return results


def nhk_time(s):
    """'7:00pm' → '19:00'"""
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap]m)?", s.strip().lower())
    if not m:
        return s
    h, mi, ap = int(m[1]), m[2], m[3]
    if ap == "pm" and h < 12:
        h += 12
    return f"{h:02d}:{mi}"


# ---------------------------------------------------------------- 都響
def scrape_tmso():
    base = "https://www.tmso.or.jp"
    results = []
    for page in range(1, 30):
        soup = BeautifulSoup(get(f"{base}/j/concert/", params={"tab": 1, "page": page}).text, "html.parser")
        entries = soup.select("div.entry-content")
        if not entries:
            break
        for e in entries:
            link = e.select_one("a.entry-link")
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", e.select_one(".entry-date").get_text())
            if not link or not m:
                continue
            d = date(int(m[1]), int(m[2]), int(m[3]))
            tm = re.search(r"(\d{1,2}:\d{2})開演", e.get_text())
            detail = e.select_one(".entry-detail")
            notes = detail.select_one("p.notes") if detail else None
            perf_p = detail.find("p", class_=lambda c: c != "notes") if detail else None
            tag = e.select_one(".tag-entry")
            results.append(concert(
                "都響", d, tm[1].zfill(5) if tm else "", clean(link.get_text()),
                clean(e.select_one(".entry-hall").get_text()) if e.select_one(".entry-hall") else "",
                lines_of(perf_p), lines_of(notes), urljoin(base, link["href"]),
                clean(tag.get_text()) if tag else "",
            ))
        if not soup.find("a", href=re.compile(rf"page={page + 1}\b")):
            break
    return results


# ---------------------------------------------------------------- 東響
def scrape_tso():
    results = []
    url = "https://tokyosymphony.jp/concert/"
    for page in range(1, 30):
        soup = BeautifulSoup(get(url).text, "html.parser")
        for a in soup.select("a.concertslist__box-link"):
            text = lambda sel: clean(a.select_one(sel).get_text()) if a.select_one(sel) else ""
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text(".concertslist__contents-date"))
            if not m:
                continue
            d = date(int(m[1]), int(m[2]), int(m[3]))
            if d < TODAY:
                continue
            tm = re.search(r"\d{1,2}:\d{2}", text(".concertslist__contents-time"))
            performers = [l for p in a.select(".concertslist__artist-items") for l in lines_of(p)]
            program = [l for p in a.select(".concertslist__program-items") for l in lines_of(p)]
            title = text(".concertslist__contents-title")
            results.append(concert(
                "東響", d, tm[0].zfill(5) if tm else "", title, text(".concertslist__contents-location"),
                performers, program, a["href"], tso_series(title),
            ))
        nxt = soup.find("a", href=re.compile(rf"/concert/page/{page + 1}/"))
        if not nxt:
            break
        url = nxt["href"]
    return results


def tso_series(title):
    for key in ["定期演奏会", "川崎定期", "名曲全集", "東京オペラシティシリーズ", "こども定期", "特別演奏会"]:
        if key in title:
            return key
    return ""


# ---------------------------------------------------------------- おけぴ
OKEPI_KEYWORDS = {
    "N響": ["N響", "NHK交響楽団"],
    "都響": ["都響", "東京都交響楽団"],
    "東響": ["東響", "東京交響楽団"],
}
OKEPI_MAX_PAGES = 3


def scrape_okepi():
    posts = {}
    for orch, keywords in OKEPI_KEYWORDS.items():
        for kw in keywords:
            for page in range(1, OKEPI_MAX_PAGES + 1):
                params = {"Keyword": kw, "SortKey": "UpdateTimeDesc"}
                if page > 1:
                    params.update(PageNo=page, Paging="True")
                soup = BeautifulSoup(get("https://okepi.net/bbs/posting/index", params=params).text, "html.parser")
                items = soup.select("a.list-group-item[href*='/bbs/posting/detail/']")
                for a in items:
                    p = parse_okepi_item(a, orch)
                    if p and p["id"] not in posts:
                        posts[p["id"]] = p
                if len(items) < 20:
                    break
    return sorted(posts.values(), key=lambda p: (p["date"], p["time"]))


def parse_okepi_item(a, orch):
    texts = [clean(t) for t in a.stripped_strings]
    when = next((t for t in texts if re.match(r"\d{1,2}\.\d{1,2}\(.\)", t)), None)
    if not when:
        return None
    m = re.match(r"(\d{1,2})\.(\d{1,2})\(.\)\s*(\d{1,2}:\d{2})?", when)
    month, day = int(m[1]), int(m[2])
    # 年が書かれていないので、今日以降で最も近い日付とみなす
    year = TODAY.year if (month, day) >= (TODAY.month, TODAY.day) else TODAY.year + 1
    try:
        d = date(year, month, day)
    except ValueError:
        return None

    title_el = a.select_one(".h5")
    price_el = a.select_one(".fs-price")
    seat_el = a.select_one(".fs-item.text-truncate")
    hall_el = a.select_one(".col-6 .fs-label")
    count = next((t for t in texts if re.fullmatch(r"\d+枚.*", t)), "")
    trade = clean(a.select_one(".fs-tradetype").get_text()) if a.select_one(".fs-tradetype") else ""
    title = clean(title_el.get_text()) if title_el else ""
    # 他楽団名だけにヒットした投稿（例:「東響」で「東響コーラス」）も含まれ得るが、日付で絞るので許容
    return {
        "id": a["href"].rstrip("/").split("/")[-1],
        "orchestra": orch,
        "date": d.isoformat(),
        "weekday": WEEKDAYS[d.weekday()],
        "time": (m[3] or "").zfill(5) if m[3] else "",
        "title": title,
        "hall": clean(hall_el.get_text()) if hall_el else "",
        "seat": clean(seat_el.get_text()) if seat_el else "",
        "count": count,
        "trade": trade,
        "price": int(price_el.get_text().replace(",", "")) if price_el and price_el.get_text().strip().replace(",", "").isdigit() else None,
        "discounted": "値下げ" in texts,
        "url": "https://okepi.net" + a["href"],
    }


# ---------------------------------------------------------------- まとめ
def attach_okepi(concerts, posts):
    """楽団と日付（時刻が分かれば時刻も）が一致する公演におけぴの投稿IDを紐付ける。"""
    index = {}
    for c in concerts:
        index.setdefault((c["orchestra"], c["date"]), []).append(c)
    for p in posts:
        cands = index.get((p["orchestra"], p["date"]), [])
        if len(cands) > 1 and p["time"]:
            cands = [c for c in cands if c["time"] == p["time"]] or cands
        if cands:
            p["concert_key"] = key_of(cands[0])
            cands[0].setdefault("okepi", []).append(p["id"])
    # 公演に紐付かず、タイトルに楽団名もない投稿（本文にだけ名前が出る別公演など）は除く
    return [p for p in posts if "concert_key" in p
            or any(kw in p["title"] for kw in OKEPI_KEYWORDS[p["orchestra"]])]


def key_of(c):
    return f'{c["orchestra"]}|{c["date"]}|{c["time"]}|{c["title"]}'


def main():
    sources = {"N響": scrape_nhk, "都響": scrape_tmso, "東響": scrape_tso}
    concerts, errors = [], {}
    for name, fn in sources.items():
        print(f"{name} を取得中…", flush=True)
        try:
            got = [c for c in fn() if c["date"] >= TODAY.isoformat()]
            print(f"  {len(got)} 件")
            concerts += got
        except Exception as e:  # 1サイトが落ちても他は続ける
            errors[name] = str(e)
            print(f"  失敗: {e}", file=sys.stderr)

    print("おけぴ を取得中…", flush=True)
    try:
        posts = scrape_okepi()
        print(f"  {len(posts)} 件")
    except Exception as e:
        posts = []
        errors["おけぴ"] = str(e)
        print(f"  失敗: {e}", file=sys.stderr)

    # 同じ公演が重複して載ることがあるので除く
    concerts = list({key_of(c): c for c in concerts}.values())
    concerts.sort(key=lambda c: (c["date"], c["time"], c["orchestra"]))
    for c in concerts:
        c["key"] = key_of(c)
    posts = attach_okepi(concerts, posts)

    print("発売情報 を取得中…", flush=True)
    import sales  # sales.py は scraper.py の関数を使うので、ここで読み込む
    OUT.parent.mkdir(exist_ok=True)
    sale_list, news, sale_errors = sales.collect(concerts)
    errors.update(sale_errors)
    print(f"  発売日 {len(sale_list)} 件 / ニュース {len(news)} 件")

    OUT.write_text(json.dumps({
        "updated": datetime.now().isoformat(timespec="minutes"),
        "concerts": concerts,
        "okepi": posts,
        "sales": sale_list,
        "news": news,
        "errors": errors,
    }, ensure_ascii=False, indent=1))
    print(f"保存しました: {OUT}（公演 {len(concerts)} 件 / おけぴ {len(posts)} 件 / 発売日 {len(sale_list)} 件）")


if __name__ == "__main__":
    main()
