import time
import threading
from flask import Flask, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)
TARGET = "https://ssvid.net"

debug_data = {"done": False, "inputs": [], "buttons": [], "scripts": [], "turnstile": [], "html": "", "error": ""}

def inspect_page():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            print("[INFO] Loading page...", flush=True)
            page.goto(TARGET + "/en/youtube-video-downloader-4", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            inputs = page.eval_on_selector_all("input", """
                els => els.map(e => ({
                    type: e.type, name: e.name, id: e.id,
                    class: e.className, placeholder: e.placeholder,
                    visible: e.offsetParent !== null
                }))
            """)
            buttons = page.eval_on_selector_all("button, [type='submit']", """
                els => els.map(e => ({
                    tag: e.tagName, type: e.type, id: e.id,
                    class: e.className, text: e.innerText.trim().slice(0, 80),
                    visible: e.offsetParent !== null
                }))
            """)
            scripts = page.eval_on_selector_all("script[src]", """
                els => els.map(e => e.src).filter(s =>
                    s.includes('cloudflare') || s.includes('turnstile') || s.includes('challenge')
                )
            """)
            turnstile = page.eval_on_selector_all("[data-sitekey], .cf-turnstile, #turnstile-container", """
                els => els.map(e => ({
                    id: e.id, class: e.className,
                    sitekey: e.dataset.sitekey || e.dataset.key || '',
                    outer: e.outerHTML.slice(0, 300)
                }))
            """)
            html = page.content()

            debug_data.update({
                "done": True,
                "inputs": inputs,
                "buttons": buttons,
                "scripts": scripts,
                "turnstile": turnstile,
                "html": html[:10000],
                "error": ""
            })

            print(f"[INPUTS] {inputs}", flush=True)
            print(f"[BUTTONS] {buttons}", flush=True)
            print(f"[SCRIPTS] {scripts}", flush=True)
            print(f"[TURNSTILE] {turnstile}", flush=True)
            browser.close()

    except Exception as e:
        debug_data["error"] = str(e)
        print(f"[ERR] {e}", flush=True)

threading.Thread(target=inspect_page, daemon=True).start()

@app.route("/")
def index():
    return jsonify({"ok": True, "done": debug_data["done"]})

@app.route("/debug")
def debug():
    return jsonify(debug_data)

@app.route("/html")
def html():
    from flask import Response
    return Response(debug_data["html"], mimetype="text/html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
