"""Magazyn ogłoszeń (SQLite w folderze danych skryptu). Bezpieczny dla wielu wątków (pętla + panel WWW)."""
from datetime import datetime, timezone
import json
import sqlite3
import threading

STATUSES = ('new', 'seen', 'fav', 'rejected')
FIELDS = ('url', 'title', 'short', 'make', 'model', 'version', 'year', 'mileage', 'engine_cc', 'power_hp', 'fuel', 'gearbox',
          'price', 'currency', 'city', 'region', 'thumb', 'seller', 'created_at')
SORTS = {
    'found': 'first_seen DESC',
    'created': 'created_at DESC',
    'price': 'price IS NULL, price ASC',
    'price_desc': 'price DESC',
    'mileage': 'mileage IS NULL, mileage ASC',
    'year': 'year DESC',
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS ads (
                id TEXT PRIMARY KEY, search TEXT, url TEXT, title TEXT, short TEXT, make TEXT, model TEXT, version TEXT,
                year INTEGER, mileage INTEGER, engine_cc INTEGER, power_hp INTEGER, fuel TEXT, gearbox TEXT,
                price INTEGER, currency TEXT, city TEXT, region TEXT, thumb TEXT, seller TEXT, created_at TEXT,
                first_seen TEXT, last_seen TEXT, status TEXT NOT NULL DEFAULT 'new', note TEXT NOT NULL DEFAULT '',
                baseline INTEGER NOT NULL DEFAULT 0, price_history TEXT NOT NULL DEFAULT '[]')''')
            self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
            self.db.execute('''CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, filters TEXT NOT NULL DEFAULT '{}',
                url TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created TEXT, updated TEXT)''')

    # --- meta ---
    def get_meta(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default

    def set_meta(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                            (key, str(value)))

    # --- wyszukiwania (z panelu) ---
    def searches(self, enabled_only=False):
        sql = 'SELECT * FROM searches' + (' WHERE enabled = 1' if enabled_only else '') + ' ORDER BY id'
        with self.lock:
            rows = self.db.execute(sql).fetchall()
        return [dict(r, filters=json.loads(r['filters'] or '{}'), enabled=bool(r['enabled'])) for r in rows]

    def save_search(self, name, filters, url, enabled=True, search_id=None):
        name = (name or '').strip()[:60]
        if not name:
            raise ValueError('Podaj nazwę wyszukiwania.')
        stamp = now()
        with self.lock, self.db:
            try:
                if search_id:
                    old = self.db.execute('SELECT name FROM searches WHERE id = ?', (int(search_id),)).fetchone()
                    if not old:
                        raise ValueError('Nie ma takiego wyszukiwania.')
                    self.db.execute('UPDATE searches SET name = ?, filters = ?, url = ?, enabled = ?, updated = ? WHERE id = ?',
                                    (name, json.dumps(filters, ensure_ascii=False), url, int(bool(enabled)), stamp, int(search_id)))
                    if old['name'] != name:
                        self.db.execute('UPDATE ads SET search = ? WHERE search = ?', (name, old['name']))
                    return int(search_id)
                cur = self.db.execute('INSERT INTO searches (name, filters, url, enabled, created, updated) VALUES (?, ?, ?, ?, ?, ?)',
                                      (name, json.dumps(filters, ensure_ascii=False), url, int(bool(enabled)), stamp, stamp))
                return cur.lastrowid
            except sqlite3.IntegrityError:
                raise ValueError(f'Wyszukiwanie o nazwie „{name}” już istnieje.') from None

    def delete_search(self, search_id):
        with self.lock, self.db:
            return self.db.execute('DELETE FROM searches WHERE id = ?', (int(search_id),)).rowcount > 0

    # --- ogłoszenia ---
    def upsert(self, ad, search, baseline=False):
        """Zapisuje ogłoszenie. Zwraca ('new' | 'price_drop' | 'price_up' | 'same', poprzednia_cena)."""
        stamp = now()
        with self.lock, self.db:
            row = self.db.execute('SELECT price, price_history FROM ads WHERE id = ?', (ad['id'],)).fetchone()
            if row is None:
                history = json.dumps([[stamp, ad.get('price')]])
                values = [ad.get(f) for f in FIELDS]
                self.db.execute(f'''INSERT INTO ads (id, search, {", ".join(FIELDS)}, first_seen, last_seen, status, baseline, price_history)
                                    VALUES (?, ?, {", ".join("?" for _ in FIELDS)}, ?, ?, ?, ?, ?)''',
                                [ad['id'], search, *values, stamp, stamp, 'seen' if baseline else 'new', int(baseline), history])
                return 'new', None
            old = row['price']
            change = 'same'
            history = json.loads(row['price_history'] or '[]')
            if ad.get('price') is not None and old is not None and ad['price'] != old:
                change = 'price_drop' if ad['price'] < old else 'price_up'
                history.append([stamp, ad['price']])
            updates = {f: ad.get(f) for f in FIELDS if ad.get(f) is not None}
            sets = ', '.join(f'{k} = ?' for k in updates)
            self.db.execute(f'UPDATE ads SET {sets}, last_seen = ?, price_history = ? WHERE id = ?',
                            [*updates.values(), stamp, json.dumps(history), ad['id']])
            return change, old

    def set_status(self, ad_id, status):
        if status not in STATUSES:
            raise ValueError(f'Nieznany status: {status}')
        with self.lock, self.db:
            return self.db.execute('UPDATE ads SET status = ? WHERE id = ?', (status, str(ad_id))).rowcount > 0

    def set_note(self, ad_id, note):
        with self.lock, self.db:
            return self.db.execute('UPDATE ads SET note = ? WHERE id = ?', ((note or '')[:1000], str(ad_id))).rowcount > 0

    def get(self, ad_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM ads WHERE id = ?', (str(ad_id),)).fetchone()
        return _row(row) if row else None

    def query(self, status=None, search=None, text=None, sort='found', limit=200, offset=0):
        where, args = [], []
        if status in STATUSES:
            where.append('status = ?')
            args.append(status)
        if search:
            where.append('search = ?')
            args.append(search)
        if text:
            where.append("(title || ' ' || IFNULL(version,'') || ' ' || IFNULL(city,'') || ' ' || IFNULL(note,'') || ' ' || short) LIKE ?")
            args.append(f'%{text}%')
        sql = 'SELECT * FROM ads' + (' WHERE ' + ' AND '.join(where) if where else '')
        sql += f' ORDER BY {SORTS.get(sort, SORTS["found"])} LIMIT ? OFFSET ?'
        with self.lock:
            rows = self.db.execute(sql, [*args, max(1, min(int(limit), 500)), max(0, int(offset))]).fetchall()
        return [_row(r) for r in rows]

    def delete_ads(self, search, keep_fav=True):
        """Usuwa ogłoszenia złapane przez wyszukiwanie (ulubione zostają). Zwraca liczbę usuniętych."""
        with self.lock, self.db:
            return self.db.execute('DELETE FROM ads WHERE search = ?' + (" AND status != 'fav'" if keep_fav else ''),
                                   (search,)).rowcount

    def fav_counts(self):
        with self.lock:
            rows = self.db.execute("SELECT search, COUNT(*) AS n FROM ads WHERE status = 'fav' GROUP BY search").fetchall()
        return {r['search']: r['n'] for r in rows}

    def counts(self):
        with self.lock:
            rows = self.db.execute('SELECT status, COUNT(*) AS n FROM ads GROUP BY status').fetchall()
            searches = self.db.execute('SELECT search, COUNT(*) AS n FROM ads GROUP BY search').fetchall()
        out = {s: 0 for s in STATUSES}
        out.update({r['status']: r['n'] for r in rows})
        return {'status': out, 'searches': {r['search']: r['n'] for r in searches}}


def _row(row):
    item = dict(row)
    item['price_history'] = json.loads(item.get('price_history') or '[]')
    return item
