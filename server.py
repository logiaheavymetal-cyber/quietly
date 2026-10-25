#!/usr/bin/env python3
"""Quietly: servidor intermedio entre la interfaz y SearXNG (solo biblioteca estándar de Python)."""
import os, re, json, hmac, hashlib, secrets, socket, ipaddress, threading, base64
import urllib.request, urllib.parse, urllib.error
from collections import OrderedDict
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get('PORT', '3000'))
SEARX = os.environ.get('SEARX_URL', 'http://127.0.0.1:8080').rstrip('/')
RATE = float(os.environ.get('RATE_PER_MIN', '40'))
TRUST = os.environ.get('TRUST_PROXY') == '1'
TTL = 600
KEY = secrets.token_bytes(32)
SALT = [secrets.token_bytes(16)]
BASE = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(BASE, 'public', 'index.html'), encoding='utf-8').read()
HTML = re.sub(r'<link[^>]*fonts\.googleapis[^>]*>', '', HTML).replace("ls('q_url')||''", "ls('q_url')||location.origin").encode()
HDR = {
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'Permissions-Policy': 'geolocation=(), camera=(), microphone=()',
    'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'",
}
lock = threading.Lock()
cache = OrderedDict()
hits = {}

def tick():
    with lock:
        hits.clear()
    t = threading.Timer(60, tick); t.daemon = True; t.start()
tick()

def daily():
    SALT[0] = secrets.token_bytes(16)
    t = threading.Timer(86400, daily); t.daemon = True; t.start()
t0 = threading.Timer(86400, daily); t0.daemon = True; t0.start()

def sign(u):
    return base64.urlsafe_b64encode(hmac.new(KEY, u.encode(), hashlib.sha256).digest()).decode()[:22]

def cget(k):
    with lock:
        v = cache.get(k)
        if v and v[0] > __import__('time').time():
            return v[1]
        cache.pop(k, None)

def cset(k, d):
    with lock:
        cache[k] = (__import__('time').time() + TTL, d)
        while len(cache) > 500:
            cache.popitem(last=False)

def search(q, cat, page, safe):
    k = (q, cat, page, safe)
    d = cget(k)
    if d is None:
        url = SEARX + '/search?' + urllib.parse.urlencode({'q': q, 'format': 'json', 'categories': cat, 'pageno': page, 'safesearch': safe})
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept': 'application/json'}), timeout=8) as r:
            raw = json.load(r).get('results', [])
        out = []
        for x in raw[:30]:
            im = x.get('thumbnail_src') or x.get('thumbnail') or x.get('img_src') or ''
            th = None
            if isinstance(im, str) and re.match(r'https?:', im):
                th = '/img?u=' + urllib.parse.quote(im, safe='') + '&s=' + sign(im)
            out.append({'title': str(x.get('title') or '')[:200], 'url': x.get('url'),
                        'content': str(x.get('content') or '')[:300], 'filesize': x.get('filesize'), 'thumbnail_src': th})
        d = {'results': out}
        cset(k, d)
    return d

class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
OPENER = urllib.request.build_opener(NoRedir)

def fetch_img(u, depth=0):
    p = urllib.parse.urlsplit(u)
    if p.scheme not in ('http', 'https') or not p.hostname or depth > 3:
        return 400, None, None
    try:
        for ai in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80)):
            if not ipaddress.ip_address(ai[4][0]).is_global:
                return 403, None, None
    except Exception:
        return 403, None, None
    req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'image/*'})
    try:
        r = OPENER.open(req, timeout=6)
    except urllib.error.HTTPError as e:
        loc = e.headers.get('Location')
        if 300 <= e.code < 400 and loc:
            return fetch_img(urllib.parse.urljoin(u, loc), depth + 1)
        return 404, None, None
    except Exception:
        return 502, None, None
    ct = r.headers.get('Content-Type', '')
    if not re.match(r'image/(png|jpe?g|gif|webp|avif)', ct):
        return 404, None, None
    data = r.read(3000001)
    if len(data) > 3000000:
        return 413, None, None
    return 200, ct, data

class H(BaseHTTPRequestHandler):
    server_version = 'Quietly'
    def log_message(self, *a):
        pass
    def out(self, code, body=b'', ctype='text/plain', extra=None):
        self.send_response(code)
        for k, v in HDR.items():
            self.send_header(k, v)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
    def js(self, code, d):
        self.out(code, json.dumps(d).encode(), 'application/json', {'Cache-Control': 'no-store'})
    def limited(self, cost):
        ip = self.client_address[0]
        if TRUST:
            ip = (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip() or ip
        i = hashlib.sha256(SALT[0] + ip.encode()).hexdigest()[:12]
        with lock:
            hits[i] = hits.get(i, 0) + cost
            return hits[i] > RATE
    def do_GET(self):
        try:
            u = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(u.query)
            g = lambda n, d='': (q.get(n) or [d])[0]
            if u.path == '/':
                return self.out(200, HTML, 'text/html; charset=utf-8', {'Cache-Control': 'no-cache'})
            if u.path == '/health':
                return self.out(200, b'ok')
            if u.path == '/search':
                if self.limited(1):
                    return self.js(429, {'error': 'rate'})
                qq = g('q').strip()[:200]
                if not qq:
                    return self.js(400, {'error': 'q'})
                cat = g('categories') if g('categories') in ('general', 'images', 'files') else 'general'
                page = min(max(int(g('pageno', '1')) if g('pageno', '1').isdigit() else 1, 1), 10)
                safe = g('safesearch') if g('safesearch') in ('0', '1', '2') else '1'
                return self.js(200, search(qq, cat, page, safe))
            if u.path == '/img':
                if self.limited(0.1):
                    return self.out(429)
                t = g('u')
                if not hmac.compare_digest(g('s'), sign(t)):
                    return self.out(403)
                code, ct, data = fetch_img(t)
                if code != 200:
                    return self.out(code)
                return self.out(200, data, ct, {'Cache-Control': 'public, max-age=86400'})
            self.out(404)
        except Exception:
            try:
                self.js(502, {'error': 'fail'})
            except Exception:
                pass

if __name__ == '__main__':
    s = ThreadingHTTPServer(('0.0.0.0', PORT), H)
    s.daemon_threads = True
    print('Quietly escuchando en el puerto', PORT, flush=True)
    s.serve_forever()
