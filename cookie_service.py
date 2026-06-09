import cloudscraper
import time
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)
TARGET = "https://ssvid.net"

cookie_cache = {
    "cookies": {},
    "cookie_string": "",
    "updated_at": 0,
    "last_error": "",
    "last_status": 0
}

REFRESH_INTERVAL = 300

def make_scraper():
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
        delay=10
    )

def refresh_cookies():
    while True:
        scraper = make_scraper()  # fresh scraper each time
        try:
            r = scraper.get(
                TARGET + "/en/youtube-video-downloader-4",
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                }
            )

            cookie_cache["last_status"] = r.status_code
            print(f"[INFO] Status: {r.status_code}, URL: {r.url}", flush=True)
            print(f"[INFO] Response length: {len(r.text)}", flush=True)

            cookies = dict(scraper.cookies)
            print(f"[INFO] Cookies found: {list(cookies.keys())}", flush=True)

            if cookies:
                cookie_cache.update({
                    "cookies": cookies,
                    "cookie_string": "; ".join(f"{k}={v}" for k, v in cookies.items()),
                    "updated_at": time.time(),
                    "last_error": ""
                })
                print(f"[OK] Cookies refreshed: {list(cookies.keys())}", flush=True)
            else:
                # Even if no cookies, store session cookies from response
                resp_cookies = dict(r.cookies)
                print(f"[INFO] Response cookies: {list(resp_cookies.keys())}", flush=True)
                if resp_cookies:
                    cookie_cache.update({
                        "cookies": resp_cookies,
                        "cookie_string": "; ".join(f"{k}={v}" for k, v in resp_cookies.items()),
                        "updated_at": time.time(),
                        "last_error": ""
                    })
                    print(f"[OK] Used response cookies: {list(resp_cookies.keys())}", flush=True)
                else:
                    cookie_cache["last_error"] = f"No cookies returned. Status: {r.status_code}"
                    print(f"[WARN] No cookies at all. Status={r.status_code}", flush=True)

        except Exception as e:
            cookie_cache["last_error"] = str(e)
            print(f"[ERR] {e}", flush=True)

        time.sleep(REFRESH_INTERVAL)

threading.Thread(target=refresh_cookies, daemon=True).start()
time.sleep(6)

@app.route("/")
@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "has_cookies": bool(cookie_cache["cookie_string"]),
        "age_seconds": int(time.time() - cookie_cache["updated_at"]),
        "last_error": cookie_cache["last_error"],
        "last_status": cookie_cache["last_status"]
    })

@app.route("/cookies")
def get_cookies():
    return jsonify({
        "cookie_string": cookie_cache["cookie_string"],
        "cookies": cookie_cache["cookies"],
        "age_seconds": int(time.time() - cookie_cache["updated_at"]),
        "fresh": (time.time() - cookie_cache["updated_at"]) < REFRESH_INTERVAL,
        "last_error": cookie_cache["last_error"],
        "last_status": cookie_cache["last_status"]
    })

@app.route("/refresh")
def manual_refresh():
    """Manually trigger a cookie refresh"""
    cookie_cache["updated_at"] = 0  # force refresh on next cycle
    threading.Thread(target=refresh_cookies_once).start()
    return jsonify({"ok": True, "message": "Refresh triggered"})

def refresh_cookies_once():
    scraper = make_scraper()
    try:
        r = scraper.get(TARGET + "/en/youtube-video-downloader-4", timeout=30)
        cookies = dict(scraper.cookies) or dict(r.cookies)
        if cookies:
            cookie_cache.update({
                "cookies": cookies,
                "cookie_string": "; ".join(f"{k}={v}" for k, v in cookies.items()),
                "updated_at": time.time(),
                "last_error": ""
            })
            print(f"[OK] Manual refresh done: {list(cookies.keys())}", flush=True)
    except Exception as e:
        cookie_cache["last_error"] = str(e)
        print(f"[ERR] Manual refresh: {e}", flush=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
