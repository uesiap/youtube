import cloudscraper
import time
import threading
from flask import Flask, jsonify

app = Flask(__name__)
TARGET = "https://ssvid.net"

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

cookie_cache = {
    "cookies": {},
    "cookie_string": "",
    "updated_at": 0
}

REFRESH_INTERVAL = 300  # 5 minutes

def refresh_cookies():
    while True:
        try:
            r = scraper.get(TARGET + "/en/youtube-video-downloader-4", timeout=15)
            cookies = dict(scraper.cookies)
            cookie_cache.update({
                "cookies": cookies,
                "cookie_string": "; ".join(f"{k}={v}" for k, v in cookies.items()),
                "updated_at": time.time()
            })
            print(f"[OK] Cookies refreshed at {time.ctime()}", flush=True)
        except Exception as e:
            print(f"[ERR] {e}", flush=True)
        time.sleep(REFRESH_INTERVAL)

threading.Thread(target=refresh_cookies, daemon=True).start()
time.sleep(4)  # wait for first fetch

@app.route("/")
@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "has_cookies": bool(cookie_cache["cookie_string"]),
        "age_seconds": int(time.time() - cookie_cache["updated_at"])
    })

@app.route("/cookies")
def get_cookies():
    return jsonify({
        "cookie_string": cookie_cache["cookie_string"],
        "cookies": cookie_cache["cookies"],
        "age_seconds": int(time.time() - cookie_cache["updated_at"]),
        "fresh": (time.time() - cookie_cache["updated_at"]) < REFRESH_INTERVAL
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
