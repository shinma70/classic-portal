"""ポータルをローカルで開くためのサーバー。

使い方: python3 server.py  →  http://localhost:8765 を開く
画面の「最新に更新」ボタンで scraper.py を実行し直す。
"""
import json
import subprocess
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = 8765
lock = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kw):
        super().__init__(*args, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if self.path != "/api/refresh":
            self.send_error(404)
            return
        if not lock.acquire(blocking=False):
            self.reply(409, {"ok": False, "log": "更新中です。しばらくお待ちください。"})
            return
        try:
            proc = subprocess.run([sys.executable, str(ROOT / "scraper.py")],
                                  capture_output=True, text=True, timeout=900)
            self.reply(200 if proc.returncode == 0 else 500,
                       {"ok": proc.returncode == 0, "log": proc.stdout + proc.stderr})
        finally:
            lock.release()

    def reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    if not (ROOT / "data" / "concerts.json").exists():
        print("初回のデータ取得を行います…")
        subprocess.run([sys.executable, str(ROOT / "scraper.py")])
    url = f"http://localhost:{PORT}"
    print(f"{url} で公開中（終了は Ctrl+C）")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
