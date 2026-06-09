import time
import threading
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

cache_lock = threading.Lock()


def cookies_to_header(cookies):
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def save_cookie_data(cookie_dict, cookie_string, error="", status=200):
    with cache_lock:
        cookie_cache.update({
            "cookies": cookie_dict,
            "cookie_string": cookie_string,
            "updated_at": time.time(),
            "last_error": error,
            "last_status": status
        })


def get_cookie_data():
    with cache_lock:
        return dict(cookie_cache)


def refresh_once():
    """
    Perform a single cookie refresh.
    """
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

                page = context.new_page()

                print("[STEP1] Visiting ssvid.net...", flush=True)

                page.goto(
                    TARGET + "/en/youtube-video-downloader-4",
                    wait_until="networkidle",
                    timeout=60000
                )

                print("[WAIT] Waiting for cookies...", flush=True)

                cookies = []

                for i in range(15):
                    cookies = context.cookies()
                    names = [c["name"] for c in cookies]

                    print(
                        f"[CHECK {i + 1}] Cookies: {names}",
                        flush=True
                    )

                    if "cf_clearance" in names:
                        print("[OK] cf_clearance found", flush=True)
                        break

                    page.wait_for_timeout(1000)

                cookie_dict = {
                    c["name"]: c["value"]
                    for c in cookies
                }

                cookie_string = cookies_to_header(cookies)

                names = list(cookie_dict.keys())

                if "cf_clearance" in names:
                    save_cookie_data(
                        cookie_dict,
                        cookie_string,
                        "",
                        200
                    )
                    print("[OK] Cookies updated", flush=True)

                else:
                    save_cookie_data(
                        cookie_dict,
                        cookie_string,
                        f"No cf_clearance. Got: {names}",
                        200
                    )
                    print(
                        f"[WARN] No cf_clearance. Got: {names}",
                        flush=True
                    )

            finally:
                browser.close()

    except Exception as e:
        with cache_lock:
            cookie_cache["last_error"] = str(e)

        print(f"[ERR] {e}", flush=True)


def refresh_loop():
    """
    Background scheduler.
    """
    while True:
        refresh_once()
        time.sleep(REFRESH_INTERVAL)


# Start ONE scheduler thread
threading.Thread(
    target=refresh_loop,
    daemon=True
).start()


@app.route("/")
@app.route("/health")
def health():
    data = get_cookie_data()

    return jsonify({
        "ok": True,
        "has_cookies": bool(data["cookie_string"]),
        "age_seconds": int(time.time() - data["updated_at"])
        if data["updated_at"] else None,
        "last_error": data["last_error"],
        "last_status": data["last_status"],
    })


@app.route("/cookies")
def get_cookies():
    data = get_cookie_data()

    return jsonify({
        "cookie_string": data["cookie_string"],
        "cookies": data["cookies"],
        "age_seconds": int(time.time() - data["updated_at"])
        if data["updated_at"] else None,
        "fresh": (
            data["updated_at"] > 0
            and (time.time() - data["updated_at"]) < REFRESH_INTERVAL
        ),
        "last_error": data["last_error"],
        "last_status": data["last_status"]
    })


@app.route("/refresh")
def manual_refresh():
    threading.Thread(
        target=refresh_once,
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "message": "Refresh started"
    })


@app.route("/search", methods=["POST"])
def search():
    try:
        body = request.get_json(silent=True) or {}

        query = (
            request.form.get("query")
            or body.get("query")
            or ""
        ).strip()

        if not query:
            return jsonify({
                "status": "error",
                "mess": "No query"
            }), 400

        data = get_cookie_data()

        if not data["cookie_string"]:
            return jsonify({
                "status": "error",
                "mess": "No cookies available yet"
            }), 503

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )

            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                )

                cookie_list = [
                    {
                        "name": k,
                        "value": v,
                        "domain": "ssvid.net",
                        "path": "/"
                    }
                    for k, v in data["cookies"].items()
                ]

                if cookie_list:
                    context.add_cookies(cookie_list)

                page = context.new_page()

                page.goto(
                    TARGET + "/en/youtube-video-downloader-4",
                    wait_until="domcontentloaded",
                    timeout=30000
                )

                page.wait_for_timeout(2000)

                input_box = page.locator("input[type='text']").first
                input_box.fill(query)

                response_waiter = page.wait_for_response(
                    lambda r: "/api/ajax/search" in r.url,
                    timeout=30000
                )

                page.keyboard.press("Enter")

                response = response_waiter

                try:
                    data = response.json()
                except Exception:
                    data = {
                        "status": "error",
                        "mess": "Response was not JSON"
                    }

                return jsonify(data)

            finally:
                browser.close()

    except PlaywrightTimeoutError:
        return jsonify({
            "status": "error",
            "mess": "Timed out waiting for search response"
        }), 504

    except Exception as e:
        return jsonify({
            "status": "error",
            "mess": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080
    )
