# cookie_service.py
import cloudscraper
import json
import time
import threading
from flask import Flask, jsonify

app = Flask(__name__)

TARGET = "https://amp3.cc"
scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)

cookie_cache = {"cookies": {}, "xsrf": "", "session": "", "updated_at": 0}
REFRESH_INTERVAL = 300  # 5 minutes

def refresh_cookies():
    while True:
        try:
            r = scraper.get(TARGET + "/", timeout=15)
            cookies = dict(scraper.cookies)
            xsrf = cookies.get("XSRF-TOKEN", "")
            session = cookies.get("ampx_session", "")
            cookie_cache.update({
                "cookies": cookies,
                "xsrf": xsrf,
                "session": session,
                "updated_at": time.time()
            })
            print(f"[OK] Cookies refreshed at {time.ctime()}")
        except Exception as e:
            print(f"[ERR] Refresh failed: {e}")
        time.sleep(REFRESH_INTERVAL)

@app.route("/cookies")
def get_cookies():
    age = time.time() - cookie_cache["updated_at"]
    return jsonify({
        "xsrf": cookie_cache["xsrf"],
        "session": cookie_cache["session"],
        "cookie_string": "; ".join(f"{k}={v}" for k, v in cookie_cache["cookies"].items()),
        "age_seconds": int(age),
        "fresh": age < REFRESH_INTERVAL
    })

@app.route("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    t = threading.Thread(target=refresh_cookies, daemon=True)
    t.start()
    time.sleep(3)  # let first refresh complete
    app.run(host="0.0.0.0", port=10000)
