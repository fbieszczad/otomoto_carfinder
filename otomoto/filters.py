"""Filtry wyszukiwania jak na otomoto: katalog (marki, modele, paliwa…) i budowanie adresu wyników z formularza."""
import json
import re
import unicodedata
from urllib.parse import quote

from . import site as S

BASE = 'https://www.otomoto.pl/osobowe'
# Pola wielokrotnego wyboru → parametr otomoto.
ENUMS = {
    'make': 'filter_enum_make', 'model': 'filter_enum_model', 'generation': 'filter_enum_generation',
    'body_type': 'filter_enum_body_type', 'fuel_type': 'filter_enum_fuel_type', 'gearbox': 'filter_enum_gearbox',
    'transmission': 'filter_enum_transmission', 'country_origin': 'filter_enum_country_origin', 'color': 'filter_enum_color',
    'door_count': 'filter_enum_door_count',
}
RANGES = {'price': 'filter_float_price', 'year': 'filter_float_year', 'mileage': 'filter_float_mileage',
          'engine_capacity': 'filter_float_engine_capacity', 'engine_power': 'filter_float_engine_power'}
SINGLES = {'new_used': 'new_used', 'damaged': 'filter_enum_damaged', 'private_business': 'private_business', 'dist': 'dist'}
FLAGS = {'no_accident': 'filter_enum_no_accident', 'original_owner': 'filter_enum_original_owner',
         'service_record': 'filter_enum_service_record', 'registered': 'filter_enum_registered'}
FLAG_LABELS = {'no_accident': 'Bezwypadkowy', 'original_owner': 'Pierwszy właściciel', 'service_record': 'Serwisowany w ASO',
               'registered': 'Zarejestrowany w Polsce'}
RANGE_LABELS = {'price': ('Cena', 'zł'), 'year': ('Rok', ''), 'mileage': ('Przebieg', 'km'),
                'engine_capacity': ('Pojemność', 'cm³'), 'engine_power': ('Moc', 'KM')}


def slugify(text):
    text = (text or '').replace('ł', 'l').replace('Ł', 'L')
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')


def _number(value):
    digits = re.sub(r'[^\d]', '', str(value if value is not None else ''))
    return int(digits) if digits else None


def clean(filters):
    """Porządkuje dane z formularza: listy tekstów, liczby, flagi; odrzuca nieznane pola."""
    filters = filters or {}
    out = {}
    for key in ENUMS:
        values = filters.get(key) or []
        values = [values] if isinstance(values, str) else values
        values = [str(v).strip() for v in values if str(v).strip()]
        if values:
            out[key] = list(dict.fromkeys(values))[:30]
    for key in RANGES:
        low, high = _number((filters.get(key) or {}).get('from')), _number((filters.get(key) or {}).get('to'))
        if low is not None and high is not None and low > high:
            low, high = high, low
        if low is not None or high is not None:
            out[key] = {k: v for k, v in (('from', low), ('to', high)) if v is not None}
    for key in SINGLES:
        value = str(filters.get(key) or '').strip()
        if value:
            out[key] = value
    for key in FLAGS:
        if filters.get(key) in (True, 'true', '1', 1, 'on'):
            out[key] = True
    city = str(filters.get('city') or '').strip()
    if city:
        out['city'] = city[:60]
    return out


def build_url(filters):
    """Adres wyników otomoto (sortowanie: najnowsze)."""
    f = clean(filters)
    path = BASE + (f"/{slugify(f['city'])}" if f.get('city') else '')
    params = []
    for key, name in ENUMS.items():
        values = f.get(key) or []
        if len(values) == 1:
            params.append((f'search[{name}]', values[0]))
        else:
            params.extend((f'search[{name}][{i}]', v) for i, v in enumerate(values))
    for key, name in RANGES.items():
        for side in ('from', 'to'):
            if side in (f.get(key) or {}):
                params.append((f'search[{name}:{side}]', str(f[key][side])))
    for key, name in SINGLES.items():
        if key in f and not (key == 'dist' and 'city' not in f):
            params.append((f'search[{name}]', f[key]))
    for key, name in FLAGS.items():
        if f.get(key):
            params.append((f'search[{name}]', '1'))
    params.append((S.ORDER_KEY, S.NEWEST))
    return path + '?' + '&'.join(f'{quote(k, safe="")}={quote(v, safe="")}' for k, v in params)


def summary(filters, catalog=None):
    """Krótki opis filtrów po polsku, np. „Volkswagen Golf · Diesel · 20–50 tys. zł · od 2015”."""
    f = clean(filters)
    names = _names(catalog or {})
    parts = []
    makes = [names.get(('make', m), m) for m in f.get('make', [])]
    models = [names.get(('model', m), m) for m in f.get('model', [])]
    if makes or models:
        parts.append(' '.join(filter(None, [', '.join(makes), ', '.join(models)])))
    for key in ('generation', 'body_type', 'fuel_type', 'gearbox', 'transmission'):
        if f.get(key):
            parts.append(', '.join(names.get((key, v), v) for v in f[key]))
    for key, (label, unit) in RANGE_LABELS.items():
        r = f.get(key)
        if r:
            fmt = (lambda n: f'{n:,}'.replace(',', ' ')) if key != 'year' else str
            text = (f"{fmt(r['from'])}–{fmt(r['to'])}" if 'from' in r and 'to' in r
                    else f"od {fmt(r['from'])}" if 'from' in r else f"do {fmt(r['to'])}")
            parts.append(f'{label} {text}{" " + unit if unit else ""}')
    for key, label in (('new_used', None), ('damaged', None), ('private_business', None)):
        if f.get(key):
            parts.append(names.get((key, f[key]), f[key]))
    parts.extend(FLAG_LABELS[k] for k in FLAGS if f.get(k))
    if f.get('city'):
        parts.append(f"{f['city']}{' +' + f['dist'] + ' km' if f.get('dist') else ''}")
    return ' · '.join(parts) or 'Wszystkie osobowe'


def short_name(filters, catalog=None, limit=60):
    """Nazwa wyszukiwania z filtrów: pierwsze elementy opisu, bez urwanego końca."""
    name = ''
    for part in summary(filters, catalog).split(' · '):
        candidate = f'{name} · {part}' if name else part
        if len(candidate) > limit:
            break
        name = candidate
    return name or summary(filters, catalog)[:limit]


def _names(catalog):
    out = {}
    for item in catalog.get('makes', []):
        out[('make', item['id'])] = item['name']
    for items in catalog.get('models', {}).values():
        for item in items:
            out[('model', item['id'])] = item['name']
    for items in catalog.get('generations', {}).values():
        for item in items:
            out[('generation', item['id'])] = item['name']
    for key, items in catalog.get('enums', {}).items():
        for item in items:
            out[(key, item['id'])] = item['name']
    return out


# ---------------------------------------------------------------------------
# Katalog z danych strony otomoto
# ---------------------------------------------------------------------------

def _values(state):
    return [{'id': v['id'], 'name': v['name'], **({'count': v['counter']} if v.get('counter') is not None else {})}
            for group in state.get('values') or [] for v in group.get('values') or []]


def catalog_from_html(html):
    match = S.NEXT_DATA.search(html or '')
    if not match:
        raise S.ParseError('Brak danych strony otomoto.')
    state = json.loads(match.group(1))['props']['pageProps']['urqlState']
    for entry in state.values():
        data = entry.get('data')
        data = json.loads(data) if isinstance(data, str) else data
        if isinstance(data, dict) and isinstance(data.get('filters'), dict):
            return _catalog(data['filters']['states'])
    raise S.ParseError('Na stronie nie ma listy filtrów.')


def _catalog(states):
    by_id = {}
    catalog = {'makes': [], 'models': {}, 'generations': {}, 'enums': {}, 'ranges': {}}
    for s in states:
        cond = {c['filterId']: c['value'] for c in s.get('conditions') or []}
        values = _values(s)
        fid = s['filterId']
        if fid == 'filter_enum_model' and 'filter_enum_make' in cond:
            catalog['models'][cond['filter_enum_make']] = values
        elif fid == 'filter_enum_generation' and 'filter_enum_model' in cond:
            catalog['generations'][f"{cond.get('filter_enum_make')}/{cond['filter_enum_model']}"] = values
        elif not cond:
            by_id[fid] = values
    catalog['makes'] = by_id.get('filter_enum_make', [])
    for key, fid in {**ENUMS, **SINGLES}.items():
        if key not in ('make', 'model', 'generation') and fid in by_id:
            catalog['enums'][key] = [{'id': v['id'], 'name': v['name']} for v in by_id[fid]]
    for key, fid in RANGES.items():
        if fid in by_id:
            catalog['ranges'][key] = [int(v['id']) for v in by_id[fid] if str(v['id']).isdigit()]
    if not catalog['makes']:
        raise S.ParseError('Katalog filtrów jest pusty.')
    return catalog
