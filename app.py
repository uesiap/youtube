"""
app.py — yt-dlp streaming server
Optimized for Render.com + comprehensive security hardening.
"""

from flask import Flask, request, Response, stream_with_context, jsonify, abort
from functools import wraps
import yt_dlp, subprocess, shutil, os, urllib.parse
import time, re, logging
from collections import defaultdict
import threading

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SECRET_KEY      = os.environ.get('SECRET_KEY', 'change-me-in-prod')
MAX_DURATION    = int(os.environ.get('MAX_DURATION', 1800))
ALLOWED_ORIGINS = set(filter(None, os.environ.get('ALLOWED_ORIGINS', '').split(',')))

# ── ffmpeg (on PATH via Docker) ───────────────────────────────────────────────
ffmpeg_path = shutil.which('ffmpeg') or ''
ffmpeg_dir  = os.path.dirname(ffmpeg_path) if ffmpeg_path else ''
log.info("ffmpeg: %s", ffmpeg_path or "NOT FOUND")

# ── Rate limiting ─────────────────────────────────────────────────────────────
_rl_lock           = threading.Lock()
_rl_counts         = defaultdict(list)
RATE_LIMIT_WINDOW  = 60
RATE_LIMIT_INFO    = 20
RATE_LIMIT_STREAM  = 5

def _client_ip():
    xff = request.headers.get('X-Forwarded-For', '')
    return xff.split(',')[0].strip() if xff else (request.remote_addr or '0.0.0.0')

def rate_limit(max_calls):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            ip  = _client_ip()
            key = f"{fn.__name__}:{ip}"
            now = time.time()
            with _rl_lock:
                _rl_counts[key] = [t for t in _rl_counts[key] if now - t < RATE_LIMIT_WINDOW]
                if len(_rl_counts[key]) >= max_calls:
                    log.warning("Rate limit: %s", key)
                    r = jsonify({'error': 'Too many requests — slow down.'})
                    r.status_code = 429
                    r.headers['Retry-After'] = str(RATE_LIMIT_WINDOW)
                    return r
                _rl_counts[key].append(now)
            return fn(*args, **kwargs)
        return wrapper
    return decorator

# ── Bot blocking ──────────────────────────────────────────────────────────────
_BAD_UA_RE = re.compile(
    r'(bot|crawler|spider|scraper|curl|wget|python-requests|java|'
    r'go-http|okhttp|axios|libwww|mechanize|scrapy|phantomjs|headless)', re.I)
_BAD_HEADERS = {'via','x-forwarded-host','x-originating-ip',
                'x-remote-addr','x-remote-ip','forwarded-for'}

def block_bots(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ua = request.headers.get('User-Agent', '')
        if not ua or _BAD_UA_RE.search(ua):
            abort(403)
        for h in _BAD_HEADERS:
            if request.headers.get(h):
                abort(403)
        return fn(*args, **kwargs)
    return wrapper

# ── CORS ──────────────────────────────────────────────────────────────────────
@app.after_request
def apply_cors(resp):
    origin = request.headers.get('Origin', '')
    if ALLOWED_ORIGINS and origin not in ALLOWED_ORIGINS:
        resp.headers.pop('Access-Control-Allow-Origin', None)
        return resp
    if origin:
        resp.headers['Access-Control-Allow-Origin']  = origin
        resp.headers['Vary']                          = 'Origin'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp

# ── Security headers ──────────────────────────────────────────────────────────
@app.after_request
def security_headers(resp):
    h = resp.headers
    h['X-Content-Type-Options']  = 'nosniff'
    h['X-Frame-Options']         = 'DENY'
    h['Referrer-Policy']         = 'no-referrer'
    h['Permissions-Policy']      = 'geolocation=(), camera=(), microphone=()'
    if 'Cache-Control' not in h:
        h['Cache-Control'] = 'no-store'
    return resp

# ── Input validation ──────────────────────────────────────────────────────────
_ALLOWED_HOSTS = re.compile(
    r'^https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com|m\.youtube\.com)', re.I)
_QUALITY_MP3   = {'64','128','192','256','320'}
_QUALITY_MP4   = {'360p','480p','720p','1080p'}

def validate_url(url):
    url = url.strip()
    if not url:
        abort(400, 'URL required')
    if not _ALLOWED_HOSTS.match(url):
        abort(400, 'Only YouTube URLs are supported')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        abort(400, 'Invalid URL scheme')
    return url

def validate_format(fmt, quality):
    if fmt not in ('mp3', 'mp4'):
        abort(400, 'format must be mp3 or mp4')
    allowed_q = _QUALITY_MP3 if fmt == 'mp3' else _QUALITY_MP4
    if quality not in allowed_q:
        abort(400, f'quality must be one of: {", ".join(sorted(allowed_q))}')

# ── yt-dlp ────────────────────────────────────────────────────────────────────
_INFO_OPTS = {
    'quiet': True, 'no_warnings': True,
    'skip_download': True, 'socket_timeout': 10,
    'noplaylist': True,
}

def get_info(url):
    with yt_dlp.YoutubeDL(_INFO_OPTS) as ydl:
        info = ydl.extract_info(url, download=False)
    duration = info.get('duration') or 0
    if duration > MAX_DURATION:
        abort(400, f'Video too long (max {MAX_DURATION // 60} min)')
    title = info.get('title', 'download')
    safe  = ''.join(c for c in title.encode('ascii','ignore').decode()
                    if c.isalnum() or c in ' -_')[:60].strip() or 'download'
    return safe, info

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return open(os.path.join(os.path.dirname(__file__), 'index.html'), encoding='utf-8').read()

@app.route('/info', methods=['POST'])
@block_bots
@rate_limit(RATE_LIMIT_INFO)
def info_route():
    body = request.get_json(silent=True) or {}
    url  = validate_url(body.get('url', ''))
    try:
        title, _ = get_info(url)
        return jsonify({'title': title})
    except Exception as e:
        log.error("/info error: %s", e)
        return jsonify({'error': str(e)}), 500

@app.route('/stream')
@block_bots
@rate_limit(RATE_LIMIT_STREAM)
def stream_route():
    url     = validate_url(request.args.get('url', ''))
    fmt     = request.args.get('format', 'mp3')
    quality = request.args.get('quality', '192' if fmt == 'mp3' else '720p')

    validate_format(fmt, quality)

    try:
        title, _ = get_info(url)
    except Exception as e:
        log.error("/stream get_info error: %s", e)
        return str(e), 500

    filename    = urllib.parse.quote(title + '.' + fmt)
    disposition = f'attachment; filename="{title}.{fmt}"; filename*=UTF-8\'\'{filename}'

    base_flags = [
        'yt-dlp', '--no-playlist',
        '--socket-timeout', '15',
        '--ffmpeg-location', ffmpeg_dir,
        '-o', '-', '--quiet',
    ]

    if fmt == 'mp3':
        cmd  = base_flags + ['-f', 'bestaudio/best',
                             '--extract-audio', '--audio-format', 'mp3',
                             '--audio-quality', quality, url]
        mime = 'audio/mpeg'
    else:
        h    = quality.replace('p', '')
        cmd  = base_flags + ['-f',
               f'bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/best[height<={h}][ext=mp4]/best',
               '--merge-output-format', 'mp4', url]
        mime = 'video/mp4'

    def generate():
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=0)
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        except GeneratorExit:
            proc.kill()
        finally:
            proc.stdout.close()
            proc.wait()
            log.info("Stream done: %s fmt=%s q=%s", title, fmt, quality)

    return Response(
        stream_with_context(generate()),
        mimetype=mime,
        headers={
            'Content-Disposition': disposition,
            'X-Accel-Buffering':   'no',
            'Cache-Control':       'no-store',
        },
    )

@app.route('/healthz')
def healthz():
    return 'ok', 200

@app.errorhandler(400)
def bad_request(e):  return jsonify({'error': str(e.description)}), 400
@app.errorhandler(403)
def forbidden(e):    return jsonify({'error': 'Forbidden'}), 403
@app.errorhandler(429)
def too_many(e):     return jsonify({'error': 'Too many requests'}), 429
@app.errorhandler(500)
def server_error(e): return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(debug=False, port=10000, threaded=True)
