import time
import threading
from flask import Flask, jsonify
from curl_cffi import requests as cffi_requests

app = Flask(__name__)
TARGET = "https://ssvid.net"

cookie_cache = {
    "cookies": {},
    "cookie_string": "",
    "updated_at": 0,
    "last_error": "",
    "last_status": 0,
}

REFRESH_INTERVAL = 300

def make_session():
    return cffi_requests.Session(impersonate="chrome120")

def refresh_cookies():
    while True:
        session = make_session()
        try:
            # Step 1: Visit homepage to get Cloudflare clearance
            r1 = session.get(
                TARGET + "/en/youtube-video-downloader-4",
                timeout=30,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            print(f"[STEP1] Status={r1.status_code} Size={len(r1.text)}", flush=True)

            # Step 2: Hit the search API with a test URL — this triggers cookie setting
            payload = {
                "query": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "vt": "downloader",
                "cf_token": ""
            }
            r2 = session.post(
                TARGET + "/api/ajax/search",
                data=payload,
                timeout=30,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": TARGET,
                    "Referer": TARGET + "/en/youtube-video-downloader-4",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "*/*",
                }
            )
            print(f"[STEP2] Status={r2.status_code} Body={r2.text[:200]}", flush=True)

            # Collect all cookies from session after both requests
            cookies = dict(session.cookies)
            print(f"[INFO] Cookies after API call: {list(cookies.keys())}", flush=True)

            if cookies:
                cookie_cache.update({
                    "cookies": cookies,
                    "cookie_string": "; ".join(f"{k}={v}" for k, v in cookies.items()),
                    "updated_at": time.time(),
                    "last_error": "",
                    "last_status": r2.status_code
                })
                print(f"[OK] Cookies saved: {list(cookies.keys())}", flush=True)
            else:
                # No cookies but we have a valid session — store dummy marker
                # and pass the session object directly via search endpoint
                cookie_cache.update({
                    "last_error": f"No cookies but got API response: {r2.text[:100]}",
                    "last_status": r2.status_code,
                    "updated_at": time.time()
                })
                print(f"[WARN] No cookies. API said: {r2.text[:200]}", flush=True)

        except Exception as e:
            cookie_cache["last_error"] = str(e)
            print(f"[ERR] {e}", flush=True)

        time.sleep(REFRESH_INTERVAL)

threading.Thread(target=refresh_cookies, daemon=True).start()
time.sleep(10)

@app.route("/")
@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "has_cookies": bool(cookie_cache["cookie_string"]),
        "age_seconds": int(time.time() - cookie_cache["updated_at"]),
        "last_error": cookie_cache["last_error"],
        "last_status": cookie_cache["last_status"],
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
    threading.Thread(target=refresh_cookies, daemon=True).start()
    return jsonify({"ok": True, "message": "Refresh triggered"})

# ── New: proxy the actual search so PHP can call us directly ──────────────────
@app.route("/search", methods=["POST"])
def search():
    from flask import request
    query = request.form.get("query") or request.json.get("query", "") if request.is_json else request.form.get("query", "")

    if not query:
        return jsonify({"status": "error", "mess": "No query provided"})

    session = make_session()
    try:
        # Visit page first
        session.get(TARGET + "/en/youtube-video-downloader-4", timeout=30)

        # Now search
        r = session.post(
            TARGET + "/api/ajax/search",
            data={"query": query, "vt": "downloader", "cf_token": ""},
            timeout=30,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": TARGET,
                "Referer": TARGET + "/en/youtube-video-downloader-4",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            }
        )
        print(f"[SEARCH] {query[:50]} => {r.status_code} {r.text[:100]}", flush=True)
        return r.text, r.status_code, {"Content-Type": "application/json"}

    except Exception as e:
        return jsonify({"status": "error", "mess": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
