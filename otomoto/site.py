"""Otomoto: linki wyszukiwania, pobieranie stron wyników i odczyt ogłoszeń z danych strony (__NEXT_DATA__)."""
import gzip
import json
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HOSTS = {'www.otomoto.pl', 'otomoto.pl'}
ORDER_KEY = 'search[order]'
NEWEST = 'created_at_first:desc'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/130.0 Safari/537.36')
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class Blocked(RuntimeError):
    """Strona odmówiła (np. 403/429) — trzeba odczekać dłużej."""


class ParseError(RuntimeError):
    pass


def normalize_search_url(url):
    """Link wyszukiwania z otomoto z wymuszonym sortowaniem „najnowsze” i bez numeru strony."""
    url = (url or '').strip()
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or (parts.hostname or '').lower() not in HOSTS:
        raise ValueError(f'To nie jest link do wyszukiwania na otomoto.pl: {url[:80]}')
    if '/oferta/' in parts.path:
        raise ValueError('To link do pojedynczego ogłoszenia — wklej link do listy wyników (z filtrami).')
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in (ORDER_KEY, 'page')]
    query.append((ORDER_KEY, NEWEST))
    return urlunsplit(('https', 'www.otomoto.pl', parts.path, urlencode(query), ''))


def page_url(url, page):
    if page <= 1:
        return url
    return f'{url}&page={page}' if '?' in url else f'{url}?page={page}'


def parse_searches(text):
    """Linie „Nazwa | link” albo sam link → [(nazwa, link)]. Puste linie i # pomijane."""
    searches = []
    for number, line in enumerate((text or '').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, _, link = line.rpartition('|') if '|' in line else ('', '', line)
        url = normalize_search_url(link)
        searches.append(((name.strip() or describe_url(url))[:60], url))
    names = [n for n, _ in searches]
    if len(set(names)) != len(names):
        raise ValueError('Nazwy wyszukiwań muszą być różne.')
    return searches


def describe_url(url):
    """Krótka nazwa z adresu, np. „volkswagen golf od-2015”."""
    path = [p for p in urlsplit(url).path.split('/') if p and p != 'osobowe']
    return ' '.join(path) or 'wyszukiwanie'


def fetch(url, timeout=30):
    request = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'pl-PL,pl;q=0.9',
        'Accept-Encoding': 'gzip'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            return raw.decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429, 503):
            raise Blocked(f'otomoto odpowiedziało HTTP {exc.code}') from exc
        raise


def _param(node, key, numeric=False):
    for p in node.get('parameters') or []:
        if p.get('key') == key:
            if numeric:
                digits = re.sub(r'\D', '', str(p.get('value') or ''))
                return int(digits) if digits else None
            return p.get('displayValue') or p.get('value')
    return None


def ad_from_node(node):
    price = ((node.get('price') or {}).get('amount') or {})
    location = node.get('location') or {}
    thumb = node.get('thumbnail') or {}
    seller = node.get('sellerLink') or {}
    return {
        'id': str(node['id']),
        'url': node.get('url'),
        'title': node.get('title') or '',
        'short': node.get('shortDescription') or '',
        'created_at': node.get('createdAt'),
        'price': int(price['units']) if price.get('units') is not None else None,
        'currency': price.get('currencyCode') or 'PLN',
        'make': _param(node, 'make'),
        'model': _param(node, 'model'),
        'version': _param(node, 'version'),
        'year': _param(node, 'year', True),
        'mileage': _param(node, 'mileage', True),
        'engine_cc': _param(node, 'engine_capacity', True),
        'power_hp': _param(node, 'engine_power', True),
        'fuel': _param(node, 'fuel_type'),
        'gearbox': _param(node, 'gearbox'),
        'city': ((location.get('city') or {}).get('name')),
        'region': ((location.get('region') or {}).get('name')),
        'thumb': thumb.get('x2') or thumb.get('x1'),
        'seller': seller.get('name') or ('Osoba prywatna' if not seller else None),
        'promoted': bool(node.get('isPremiumTopAd')),
    }


def parse(html):
    """→ (liczba wszystkich wyników, [ogłoszenia])."""
    info = parse_search(html)
    return info['total'], info['ads']


def parse_search(html):
    """→ {'total', 'ads', 'city', 'filters'} — także do podglądu wyszukiwania w panelu."""
    match = NEXT_DATA.search(html or '')
    if not match:
        raise ParseError('Brak danych strony (__NEXT_DATA__) — otomoto mogło zmienić stronę albo pokazać weryfikację.')
    data = json.loads(match.group(1))
    state = ((data.get('props') or {}).get('pageProps') or {}).get('urqlState') or {}
    for entry in state.values():
        payload = entry.get('data') if isinstance(entry, dict) else None
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        search = (payload or {}).get('advertSearch') if isinstance(payload, dict) else None
        if search and isinstance(search.get('edges'), list):
            ads = [ad_from_node(e['node']) for e in search['edges'] if (e or {}).get('node', {}).get('id')]
            city = (((search.get('appliedLocation') or {}).get('city')) or {}).get('name')
            applied = [f.get('name') for f in search.get('appliedFilters') or []]
            return {'total': int(search.get('totalCount') or 0), 'ads': ads, 'city': city, 'filters': applied}
    raise ParseError('Na stronie nie ma listy ogłoszeń (zmiana strony otomoto?).')
