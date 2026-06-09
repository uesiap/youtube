import time
import threading
from flask import Flask, jsonify, request
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from urllib.parse import parse_qs, unquote_plus

app = Flask(__name__)

TARGET = "https://ssvid.net"
REFRESH_INTERVAL = 270

cache_lock = threading.Lock()
cookie_cache = {"cookies": {}, "cookie_string": "", "updated_at": 0, "last_error": "", "last_status": 0}
token_cache  = {"cf_token": "", "updated_at": 0}


def cookies_to_header(cookies):
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def refresh_once():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720},
                    locale="en-US",
                )

                captured = {"cf_token": "", "post_body": ""}

                def handle_request(req):
                    if "/api/ajax/search" in req.url and req.method == "POST":
                        body = req.post_data or ""
                        captured["post_body"] = body
                        print(f"[INTERCEPT] body={body[:300]}", flush=True)
                        parsed = parse_qs(body)
                        token = parsed.get("cf_token", [""])[0]
                        if token:
                            captured["cf_token"] = token
                            print(f"[TOKEN] cf_token={token[:60]}...", flush=True)

                context.on("request", handle_request)
                page = context.new_page()

                print("[STEP1] Loading page...", flush=True)
                page.goto(TARGET + "/en/youtube-video-downloader-4", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)

                print("[STEP2] Filling #search__input...", flush=True)
                page.fill("#search__input", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                page.wait_for_timeout(1000)

                print("[STEP3] Waiting for Turnstile to be ready...", flush=True)
                # Wait up to 20s for Turnstile iframe to load and be solved
                for i in range(20):
                    # Check if turnstile iframe exists and has a response token
                    token_ready = page.evaluate("""
                        () => {
                            // Check all iframes for turnstile
                            const iframes = document.querySelectorAll('iframe');
                            for (const f of iframes) {
                                if (f.src && f.src.includes('turnstile')) return true;
                            }
                            // Also check hidden input that turnstile fills
                            const inp = document.querySelector('input[name="cf-turnstile-response"], input[name="cf_token"]');
                            if (inp && inp.value) return true;
                            return false;
                        }
                    """)
                    print(f"[TURNSTILE CHECK {i+1}] ready={token_ready}", flush=True)
                    if token_ready:
                        break
                    page.wait_for_timeout(1000)

                page.wait_for_timeout(2000)

                print("[STEP4] Clicking #btn-start...", flush=True)
                page.click("#btn-start")

                print("[STEP5] Waiting for API response...", flush=True)
                # Wait up to 15s for the intercepted request
                for i in range(15):
                    if captured["cf_token"]:
                        break
                    page.wait_for_timeout(1000)
                    print(f"[WAIT {i+1}] token captured: {bool(captured['cf_token'])}", flush=True)

                cookies = context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                cookie_string = cookies_to_header(cookies)

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
                        print("[OK] Token and cookies saved!", flush=True)
                    else:
                        print(f"[WARN] No token. post_body was: {captured['post_body'][:200]}", flush=True)

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
        d = dict(cookie_cache)
        t = dict(token_cache)
    return jsonify({
        "ok": True,
        "has_cookies": bool(d["cookie_string"]),
        "has_token": bool(t["cf_token"]),
        "age_seconds": int(time.time() - d["updated_at"]) if d["updated_at"] else None,
        "last_error": d["last_error"],
    })


@app.route("/cookies")
def get_cookies():
    with cache_lock:
        d = dict(cookie_cache)
        t = dict(token_cache)
    return jsonify({
        "cookie_string": d["cookie_string"],
        "cookies": d["cookies"],
        "cf_token": t["cf_token"],
        "age_seconds": int(time.time() - d["updated_at"]) if d["updated_at"] else None,
        "fresh": d["updated_at"] > 0 and (time.time() - d["updated_at"]) < REFRESH_INTERVAL,
        "last_error": d["last_error"],
    })


@app.route("/refresh")
def manual_refresh():
    threading.Thread(target=refresh_once, daemon=True).start()
    return jsonify({"ok": True, "message": "Refresh started"})


@app.route("/search", methods=["POST"])
def search():
    body = request.get_json(silent=True) or {}
    query = (request.form.get("query") or body.get("query") or "").strip()
    if not query:
        return jsonify({"status": "error", "mess": "No query"}), 400

    with cache_lock:
        cookie_str = cookie_cache["cookie_string"]
        cf_token   = token_cache["cf_token"]
        cookies    = dict(cookie_cache["cookies"])

    if not cf_token:
        return jsonify({"status": "error", "mess": "No cf_token yet, try again shortly"}), 503

    from curl_cffi import requests as cffi_requests
    session = cffi_requests.Session(impersonate="chrome120")
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
            "Accept": "*/*",
        },
        timeout=20
    )

    result = r.json()
    print(f"[SEARCH] status={result.get('status')} query={query[:40]}", flush=True)

    # If token expired, trigger background refresh
    if result.get("status") == "cookie_required":
        threading.Thread(target=refresh_once, daemon=True).start()
        return jsonify({"status": "error", "mess": "Token expired, refreshing. Try again in 20s"}), 503

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
