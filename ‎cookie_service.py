import cloudscraper
import time
import threading
import re
from flask import Flask, jsonify

app = Flask(__name__)

TARGET = "https://amp3.cc"
scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

cookie_cache = {
    "cookies": {},
    "xsrf": "",
    "session": "",
    "csrf_token": "",   # ← actual _token from HTML meta tag
    "updated_at": 0
}
REFRESH_INTERVAL = 300

def refresh_cookies():
    while True:
        try:
            r = scraper.get(TARGET + "/", timeout=15)
            cookies = dict(scraper.cookies)

            # Extract csrf-token from HTML meta tag
            match = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
            csrf_token = match.group(1) if match else ""

            cookie_cache.update({
                "cookies": cookies,
                "xsrf": cookies.get("XSRF-TOKEN", ""),
                "session": cookies.get("ampx_session", ""),
                "csrf_token": csrf_token,
                "updated_at": time.time()
            })
            print(f"[OK] Refreshed at {time.ctime()} | csrf_token present: {bool(csrf_token)}", flush=True)
        except Exception as e:
            print(f"[ERR] Refresh failed: {e}", flush=True)
        time.sleep(REFRESH_INTERVAL)

_thread = threading.Thread(target=refresh_cookies, daemon=True)
_thread.start()
time.sleep(4)

@app.route("/")
@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "has_cookies": bool(cookie_cache["xsrf"]),
        "has_csrf": bool(cookie_cache["csrf_token"]),
        "age_seconds": int(time.time() - cookie_cache["updated_at"])
    })

@app.route("/cookies")
def get_cookies():
    age = time.time() - cookie_cache["updated_at"]
    return jsonify({
        "xsrf": cookie_cache["xsrf"],
        "session": cookie_cache["session"],
        "csrf_token": cookie_cache["csrf_token"],   # ← use this as _token
        "cookie_string": "; ".join(f"{k}={v}" for k, v in cookie_cache["cookies"].items()),
        "age_seconds": int(age),
        "fresh": age < REFRESH_INTERVAL
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
