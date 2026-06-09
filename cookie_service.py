import time
import threading
import json
from flask import Flask, jsonify, request
from playwright.sync_api import sync_playwright

app = Flask(__name__)
TARGET = "https://ssvid.net"

cookie_cache = {
    "cookies": {},
    "cookie_string": "",
    "updated_at": 0,
    "last_error": "",
    "last_status": 0,
}

REFRESH_INTERVAL = 270

def cookies_to_header(cookies):
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)

def refresh_cookies():
    while True:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ]
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720},
                    locale="en-US",
                )
                page = context.new_page()

                print("[STEP1] Visiting ssvid.net...", flush=True)
                page.goto(TARGET + "/en/youtube-video-downloader-4", wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)  # wait for CF to set cookies

                cookies = context.cookies()
                print(f"[INFO] Cookies: {[c['name'] for c in cookies]}", flush=True)

                cookie_string = cookies_to_header(cookies)
                cookie_dict = {c["name"]: c["value"] for c in cookies}

                browser.close()

            if cookie_string:
                cookie_cache.update({
                    "cookies": cookie_dict,
                    "cookie_string": cookie_string,
                    "updated_at": time.time(),
                    "last_error": "",
                    "last_status": 200
                })
                print(f"[OK] Cookies saved: {list(cookie_dict.keys())}", flush=True)
            else:
                cookie_cache["last_error"] = "Playwright returned no cookies"
                print("[WARN] No cookies from Playwright", flush=True)

        except Exception as e:
            cookie_cache["last_error"] = str(e)
            print(f"[ERR] {e}", flush=True)

        time.sleep(REFRESH_INTERVAL)

threading.Thread(target=refresh_cookies, daemon=True).start()
time.sleep(15)  # give playwright time to finish first run

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

@app.route("/search", methods=["POST"])
def search():
    query = request.form.get("query", "") or (request.get_json() or {}).get("query", "")
    if not query:
        return jsonify({"status": "error", "mess": "No query"})

    cookie_string = cookie_cache.get("cookie_string", "")
    if not cookie_string:
        return jsonify({"status": "error", "mess": "No cookies yet, try again in a few seconds"})

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            # Inject saved cookies
            cookie_list = [{"name": k, "value": v, "domain": "ssvid.net", "path": "/"} 
                           for k, v in cookie_cache["cookies"].items()]
            context.add_cookies(cookie_list)

            page = context.new_page()
            result_holder = {}

            def handle_response(response):
                if "/api/ajax/search" in response.url:
                    try:
                        result_holder["data"] = response.json()
                        print(f"[SEARCH] Got API response", flush=True)
                    except:
                        pass

            page.on("response", handle_response)
            page.goto(TARGET + "/en/youtube-video-downloader-4", wait_until="domcontentloaded", timeout=20000)

            # Fill in the URL and submit
            page.wait_for_timeout(2000)
            page.fill("input[name='query'], input[type='text'], #url-input, .url-input", query)
            page.wait_for_timeout(500)
            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)

            browser.close()

        if "data" in result_holder:
            return jsonify(result_holder["data"])
        else:
            return jsonify({"status": "error", "mess": "No API response intercepted"})

    except Exception as e:
        return jsonify({"status": "error", "mess": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
