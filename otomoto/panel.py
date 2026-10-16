"""Panel WWW: lista złapanych ogłoszeń do przeglądania i oznaczania — na komputerze i telefonie (ta sama sieć Wi-Fi).

Dostęp chroniony krótkim kluczem w adresie (?k=...), zapamiętywanym potem w ciasteczku.
"""
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import threading
from urllib.parse import parse_qs, urlsplit

from .store import SORTS, STATUSES

PAGE = Path(__file__).with_name('panel.html')


def load_token(data_dir):
    path = Path(data_dir) / 'panel-klucz.txt'
    try:
        token = path.read_text(encoding='utf-8').strip()
        if len(token) >= 6:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(6).replace('-', 'x').replace('_', 'y')[:8]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding='utf-8')
    return token


def lan_ip():
    """Adres komputera w sieci domowej (bez wysyłania czegokolwiek)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
    except OSError:
        return None


class Panel:
    def __init__(self, store, token, port=8765, lan=True, on_change=None, searches=()):
        self.store = store
        self.token = token
        self.port = port
        self.lan = lan
        self.on_change = on_change or (lambda *a: None)
        self.searches = list(searches)
        self.server = None

    def urls(self):
        local = f'http://localhost:{self.port}/?k={self.token}'
        ip = lan_ip() if self.lan else None
        return local, (f'http://{ip}:{self.port}/?k={self.token}' if ip else None)

    def start(self):
        panel = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _authorized(self, query):
                if secrets.compare_digest((query.get('k') or [''])[0], panel.token):
                    return True
                cookie = SimpleCookie(self.headers.get('Cookie') or '')
                return 'k' in cookie and secrets.compare_digest(cookie['k'].value, panel.token)

            def _send(self, code, body, kind='application/json', cookie=False):
                data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', f'{kind}; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                if cookie:
                    self.send_header('Set-Cookie', f'k={panel.token}; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly')
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                if not self._authorized(query):
                    return self._send(403, 'Brak dostępu — otwórz pełny adres panelu z kluczem (?k=…).', 'text/plain')
                if url.path == '/':
                    return self._send(200, PAGE.read_bytes(), 'text/html', cookie=True)
                if url.path == '/api/ads':
                    one = lambda k, d=None: (query.get(k) or [d])[0]
                    items = panel.store.query(status=one('status'), search=one('search'), text=one('q'),
                                              sort=one('sort', 'found'), limit=one('limit', 200), offset=one('offset', 0))
                    return self._send(200, {'ads': items})
                if url.path == '/api/stats':
                    return self._send(200, {**panel.store.counts(), 'searches_config': panel.searches,
                                            'statuses': STATUSES, 'sorts': list(SORTS)})
                return self._send(404, {'error': 'nie ma takiej strony'})

            def do_POST(self):
                url = urlsplit(self.path)
                if not self._authorized(parse_qs(url.query)):
                    return self._send(403, {'error': 'brak dostępu'})
                parts = url.path.strip('/').split('/')
                if len(parts) != 3 or parts[:2] != ['api', 'ads']:
                    return self._send(404, {'error': 'nie ma takiej strony'})
                try:
                    length = min(int(self.headers.get('Content-Length') or 0), 10_000)
                    body = json.loads(self.rfile.read(length) or b'{}')
                    ad_id = parts[2]
                    changed = False
                    if 'status' in body:
                        changed = panel.store.set_status(ad_id, body['status']) or changed
                    if 'note' in body:
                        changed = panel.store.set_note(ad_id, str(body['note'])) or changed
                except (ValueError, TypeError) as exc:
                    return self._send(400, {'error': str(exc)})
                if not changed:
                    return self._send(404, {'error': 'nie ma takiego ogłoszenia'})
                panel.on_change(ad_id, body)
                return self._send(200, {'ad': panel.store.get(ad_id)})

        host = '0.0.0.0' if self.lan else '127.0.0.1'
        self.server = ThreadingHTTPServer((host, self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True, name='panel').start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
