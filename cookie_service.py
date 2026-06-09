import time
import threading
import json
from flask import Flask, jsonify, request
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

app = Flask(__name__)

TARGET = "https://ssvid.net"
REFRESH_INTERVAL = 270

cookie_cache = {
    "cookies": {},
    "cookie_string": "",
    "updated_at": 0,
    "last_error": "",
    "last_status": 0,
}
# Store a captured working cf_token from real browser interaction
token_cache = {
    "cf_token": "",
    "updated_at": 0
}

cache_lock = threading.Lock()


def cookies_to_header(cookies):
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def refresh_once():
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
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 720},
                    locale="en-US",
                )

                # Intercept POST to /api/ajax/search to capture cf_token
                captured = {"cf_token": "", "post_body": ""}

                def handle_request(req):
                    if "/api/ajax/search" in req.url and req.method == "POST":
                        body = req.post_data or ""
                        captured["post_body"] = body
                        print(f"[INTERCEPT] POST body: {body[:300]}", flush=True)
                        # Extract cf_token from body
                        for part in body.split("&"):
                            if part.startswith("cf_token="):
                                captured["cf_token"] = part[len("cf_token="):]
                                print(f"[TOKEN] cf_token captured: {captured['cf_token'][:40]}...", flush=True)

                context.on("request", handle_request)

                page = context.new_page()

                print("[STEP1] Loading page...", flush=True)
                page.goto(
                    TARGET + "/en/youtube-video-downloader-4",
                    wait_until="networkidle",
                    timeout=60000
                )
                page.wait_for_timeout(3000)

                print("[STEP2] Filling URL input...", flush=True)
                # Try different selectors for the input
                for selector in ["input[name='query']", "input[type='text']", "input[type='url']", ".search-input", "#url"]:
                    try:
                        el = page.locator(selector).first
                        if el.is_visible():
                            el.fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                            print(f"[OK] Filled input: {selector}", flush=True)
                            break
                    except:
                        continue

                page.wait_for_timeout(2000)

                print("[STEP3] Clicking download button...", flush=True)
                for selector in ["button[type='submit']", "button.download", ".btn-download", "button"]:
                    try:
                        el = page.locator(selector).first
                        if el.is_visible():
                            el.click()
                            print(f"[OK] Clicked: {selector}", flush=True)
                            break
                    except:
                        continue

                # Wait for API call to happen
                print("[STEP4] Waiting for API call...", flush=True)
                page.wait_for_timeout(8000)

                cookies = context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                cookie_string = cookies_to_header(cookies)

                print(f"[INFO] Cookies: {list(cookie_dict.keys())}", flush=True)
                print(f"[INFO] cf_token captured: {bool(captured['cf_token'])}", flush=True)

                with cache_lock:
                    cookie_cache.update({
                        "cookies": cookie_dict,
                        "cookie_string": cookie_string,
                        "updated_at": time.time(),
                        "last_error": "" if captured["cf_token"] else "No cf_token captured",
                        "last_status": 200
                    })
                    if captured["cf_token"]:
                        token_cache["cf_token"] = captured["cf_token"]
                        token_cache["updated_at"] = time.time()
                        print(f"[OK] Token saved!", flush=True)

            finally:
                browser.close()

    except Exception as e:
        with cache_lock:
            cookie_cache["last_error"] = str(e)
        print(f"[ERR] {e}", flush=True)


def refresh_loop():
    while True:
        refresh_once()
        time.sleep(REFRESH_INTERVAL)


threading.Thread(target=refresh_loop, daemon=True).start()


@app.route("/")
@app.route("/health")
def health():
    with cache_lock:
        data = dict(cookie_cache)
        token = dict(token_cache)
    return jsonify({
        "ok": True,
        "has_cookies": bool(data["cookie_string"]),
        "has_token": bool(token["cf_token"]),
        "age_seconds": int(time.time() - data["updated_at"]) if data["updated_at"] else None,
        "last_error": data["last_error"],
        "last_status": data["last_status"],
    })


@app.route("/cookies")
def get_cookies():
    with cache_lock:
        data = dict(cookie_cache)
        token = dict(token_cache)
    return jsonify({
        "cookie_string": data["cookie_string"],
        "cookies": data["cookies"],
        "cf_token": token["cf_token"],
        "age_seconds": int(time.time() - data["updated_at"]) if data["updated_at"] else None,
        "fresh": data["updated_at"] > 0 and (time.time() - data["updated_at"]) < REFRESH_INTERVAL,
        "last_error": data["last_error"],
        "last_status": data["last_status"]
    })


@app.route("/refresh")
def manual_refresh():
    threading.Thread(target=refresh_once, daemon=True).start()
    return jsonify({"ok": True, "message": "Refresh started"})


@app.route("/search", methods=["POST"])
def search():
    try:
        body = request.get_json(silent=True) or {}
        query = (request.form.get("query") or body.get("query") or "").strip()

        if not query:
            return jsonify({"status": "error", "mess": "No query"}), 400

        with cache_lock:
            cookie_str = cookie_cache["cookie_string"]
            cf_token = token_cache["cf_token"]
            cookies = dict(cookie_cache["cookies"])

        # If we have a cf_token, try direct HTTP first (faster)
        if cf_token and cookie_str:
            from curl_cffi import requests as cffi_requests
            session = cffi_requests.Session(impersonate="chrome120")
            # inject cookies
            for k, v in cookies.items():
                session.cookies.set(k, v, domain="ssvid.net")

            r = session.post(
                TARGET + "/api/ajax/search",
                data={"query": query, "vt": "downloader", "cf_token": cf_token},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": TARGET,
                    "Referer": TARGET + "/en/youtube-video-downloader-4",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=20
            )
            result = r.json()
            print(f"[SEARCH-HTTP] {query[:40]} => {result.get('status')}", flush=True)

            if result.get("status") != "cookie_required":
                return jsonify(result)
            print("[SEARCH-HTTP] cookie_required, falling back to Playwright...", flush=True)

        # Fallback: full Playwright search
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                if cookies:
                    context.add_cookies([
                        {"name": k, "value": v, "domain": "ssvid.net", "path": "/"}
                        for k, v in cookies.items()
                    ])

                result_holder = {}

                def handle_response(resp):
                    if "/api/ajax/search" in resp.url:
                        try:
                            result_holder["data"] = resp.json()
                        except:
                            pass

                page = context.new_page()
                page.on("response", handle_response)
                page.goto(TARGET + "/en/youtube-video-downloader-4", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)

                for selector in ["input[name='query']", "input[type='text']", "input[type='url']"]:
                    try:
                        el = page.locator(selector).first
                        if el.is_visible():
                            el.fill(query)
                            break
                    except:
                        continue

                page.wait_for_timeout(1000)

                for selector in ["button[type='submit']", "button.download", "button"]:
                    try:
                        el = page.locator(selector).first
                        if el.is_visible():
                            el.click()
                            break
                    except:
                        continue

                page.wait_for_timeout(8000)

                return jsonify(result_holder.get("data", {"status": "error", "mess": "No response intercepted"}))

            finally:
                browser.close()

    except Exception as e:
        return jsonify({"status": "error", "mess": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
