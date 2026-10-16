import json
from pathlib import Path
import tempfile
import unittest
import urllib.request

from otomoto import filters as F
from otomoto import site as S
from otomoto.panel import Panel
from otomoto.store import Store
from otomoto.watcher import Config, Watcher, describe, full_title

FIXTURE = (Path(__file__).parent / 'fixture_search.html').read_text(encoding='utf-8')
URL = 'https://www.otomoto.pl/osobowe/toyota/corolla'


def ad(i, price=50000, **kw):
    base = {'id': str(i), 'url': f'https://www.otomoto.pl/osobowe/oferta/x-ID{i}.html', 'title': f'Auto {i}', 'short': '',
            'created_at': '2026-10-16T07:00:00Z', 'price': price, 'currency': 'PLN', 'make': 'Toyota', 'model': 'Corolla',
            'version': None, 'year': 2015, 'mileage': 100000, 'engine_cc': 1598, 'power_hp': 132, 'fuel': 'Benzyna',
            'gearbox': 'Manualna', 'city': 'Łódź', 'region': 'Łódzkie', 'thumb': 'https://img/x.jpg', 'seller': 'Jan', 'promoted': False}
    base.update(kw)
    return base


def page(ads, total=None):
    edges = [{'node': {'id': a['id'], 'url': a['url'], 'title': a['title'], 'createdAt': a['created_at'], 'shortDescription': '',
                       'price': {'amount': {'units': a['price'], 'currencyCode': 'PLN'}},
                       'parameters': [{'key': 'year', 'value': str(a['year']), 'displayValue': str(a['year'])}],
                       'location': {'city': {'name': 'Kraków'}}, 'thumbnail': {'x2': a['thumb']}}} for a in ads]
    data = {'advertSearch': {'totalCount': total if total is not None else len(ads), 'edges': edges}}
    state = {'k': {'data': json.dumps(data)}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({'props': {'pageProps': {'urqlState': state}}}) + '</script>'


class FakeCtx:
    def __init__(self):
        self.notifications, self.commands, self.statuses = [], [], []
        self.stopping = False

    def notify(self, title, message, **kw):
        self.notifications.append((title, message, kw))

    def poll_commands(self):
        out, self.commands = self.commands, []
        return out

    def status(self, text=None, **fields):
        self.statuses.append((text, fields))

    def sleep(self, s):
        return not self.stopping


class SiteTests(unittest.TestCase):
    def test_normalize_url_forces_newest_and_drops_page(self):
        url = S.normalize_search_url('https://otomoto.pl/osobowe/volkswagen/golf?search%5Border%5D=relevance_web&page=3&search%5Bfilter_enum_fuel_type%5D=diesel')
        self.assertTrue(url.startswith('https://www.otomoto.pl/osobowe/volkswagen/golf?'))
        self.assertIn('search%5Bfilter_enum_fuel_type%5D=diesel', url)
        self.assertIn('search%5Border%5D=created_at_first%3Adesc', url)
        self.assertNotIn('page=', url)
        self.assertNotIn('relevance', url)
        self.assertEqual(S.page_url(url, 2), url + '&page=2')
        for bad in ('https://olx.pl/x', 'https://www.otomoto.pl/osobowe/oferta/x-ID1.html', 'otomoto.pl'):
            with self.assertRaises(ValueError):
                S.normalize_search_url(bad)

    def test_parse_searches(self):
        out = S.parse_searches(f'# komentarz\nGolf | {URL}\n\n{URL}/od-2015')
        self.assertEqual([n for n, _ in out], ['Golf', 'toyota corolla od-2015'])
        self.assertEqual(S.parse_searches(''), [])
        with self.assertRaises(ValueError):
            S.parse_searches(f'A | {URL}\nA | {URL}/od-2015')

    def test_parse_real_page(self):
        total, ads = S.parse(FIXTURE)
        self.assertGreater(total, 1000)
        first = ads[0]
        self.assertEqual(first['id'], '6151172245')
        self.assertEqual((first['price'], first['year'], first['mileage'], first['engine_cc'], first['power_hp']), (33000, 2015, 242620, 1598, 132))
        self.assertEqual((first['fuel'], first['gearbox'], first['city']), ('Benzyna', 'Manualna', 'Łódź'))
        self.assertTrue(first['thumb'].startswith('https://'))
        self.assertEqual(describe(first), '2015 · 242 620 km · 1 598 cm³ 132 KM · Benzyna · Manualna')
        with self.assertRaises(S.ParseError):
            S.parse('<html>captcha</html>')
        self.assertEqual(full_title({'title': 'VW Golf 2.0 TDI', 'version': '2.0 TDI'}), 'VW Golf 2.0 TDI')
        self.assertEqual(full_title({'title': 'VW Golf', 'version': '2.0 TDI'}), 'VW Golf 2.0 TDI')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')

    def test_new_price_changes_and_status(self):
        self.assertEqual(self.store.upsert(ad(1), 'A'), ('new', None))
        self.assertEqual(self.store.upsert(ad(1), 'A'), ('same', 50000))
        self.assertEqual(self.store.upsert(ad(1, price=45000), 'A'), ('price_drop', 50000))
        self.assertEqual(self.store.upsert(ad(1, price=47000), 'A'), ('price_up', 45000))
        item = self.store.get('1')
        self.assertEqual([p for _, p in item['price_history']], [50000, 45000, 47000])
        self.assertTrue(self.store.set_status('1', 'fav'))
        self.assertTrue(self.store.set_note('1', 'dzwoniłem'))
        self.assertEqual((self.store.get('1')['status'], self.store.get('1')['note']), ('fav', 'dzwoniłem'))
        self.assertFalse(self.store.set_status('999', 'fav'))
        with self.assertRaises(ValueError):
            self.store.set_status('1', 'x')

    def test_query_and_counts(self):
        self.store.upsert(ad(1, price=30000, city='Kraków'), 'A')
        self.store.upsert(ad(2, price=20000), 'B', baseline=True)
        self.assertEqual([a['id'] for a in self.store.query(status='new')], ['1'])
        self.assertEqual([a['id'] for a in self.store.query(sort='price')], ['2', '1'])
        self.assertEqual([a['id'] for a in self.store.query(text='krak')], ['1'])
        self.assertEqual(self.store.counts()['status'], {'new': 1, 'seen': 1, 'fav': 0, 'rejected': 0})


class WatcherTests(unittest.TestCase):
    def make(self, pages):
        ctx = FakeCtx()
        cfg = Config(searches=[('Corolla', S.normalize_search_url(URL))], pages=3)
        calls = []

        def fetch(url):
            calls.append(url)
            return pages.pop(0)
        return Watcher(ctx, cfg, Store(':memory:'), fetch=fetch, sleep_between=lambda: True), ctx, calls

    def test_first_check_is_baseline_then_new_ads_notify(self):
        w, ctx, calls = self.make([page([ad(1), ad(2)]), page([ad(3), ad(1, price=40000), ad(2)])])
        self.assertEqual(w.run_check(), [])
        self.assertEqual(ctx.notifications, [])
        self.assertEqual(w.store.counts()['status']['seen'], 2)
        fresh = w.run_check()
        self.assertEqual([a['id'] for a, _ in fresh], ['3'])
        titles = [n[0] for n in ctx.notifications]
        self.assertTrue(titles[0].startswith('Auto 3 — 50 000 zł'))
        self.assertTrue(any(t.startswith('Obniżka: Auto 1') for t in titles))
        kw = ctx.notifications[0][2]
        self.assertEqual((kw['stop'], kw['image'], kw['actions'][0]), (False, 'https://img/x.jpg', ('⭐ Ulubione', 'fav 3')))

    def test_reads_several_pages_and_stops_at_end(self):
        full = [ad(i) for i in range(32)]
        w, ctx, calls = self.make([page(full, total=40), page([ad(100)], total=40)])
        w.run_check()
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].endswith('&page=2'))

    def test_many_new_ads_give_one_summary(self):
        w, ctx, calls = self.make([page([ad(0)]), page([ad(i) for i in range(1, 9)])])
        w.run_check()
        w.run_check()
        self.assertEqual(len(ctx.notifications), 1)
        self.assertTrue(ctx.notifications[0][0].startswith('8 nowych ogłoszeń'))

    def test_phone_commands(self):
        w, ctx, calls = self.make([page([ad(1)])])
        w.run_check()
        ctx.commands = ['fav 1', 'pause', 'odrzuć 999', 'hokus']
        w.handle_commands()
        self.assertEqual(w.store.get('1')['status'], 'fav')
        self.assertTrue(w.paused)

    def test_config(self):
        cfg = Config.from_settings({'searches': URL, 'interval': 10})
        self.assertEqual((cfg.interval, cfg.pages, cfg.notify_new, cfg.lan), (10, 3, True, True))
        self.assertEqual(Config.from_settings({}).searches, [])
        for bad in ({'interval': 1}, {'pages': 11}, {'searches': 'https://olx.pl/x'}):
            with self.assertRaises(ValueError):
                Config.from_settings({'searches': URL, **bad})


class PanelTests(unittest.TestCase):
    def test_token_api_and_updates(self):
        store = Store(':memory:')
        store.upsert(ad(7), 'A')
        changes = []
        panel = Panel(store, 'abcd1234', port=0, lan=False, on_change=lambda i, b: changes.append(i), searches=[('A', URL)])
        panel.start()
        port = panel.server.server_address[1]
        base = f'http://127.0.0.1:{port}'
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with self.assertRaises(urllib.error.HTTPError) as err:
                opener.open(base + '/api/ads')
            self.assertEqual(err.exception.code, 403)
            html = opener.open(base + '/?k=abcd1234')
            self.assertIn('k=abcd1234', html.headers.get('Set-Cookie'))
            data = json.loads(opener.open(base + '/api/ads?k=abcd1234&status=new').read())
            self.assertEqual([a['id'] for a in data['ads']], ['7'])
            req = urllib.request.Request(base + '/api/ads/7?k=abcd1234', data=json.dumps({'status': 'fav', 'note': 'ok'}).encode(),
                                         headers={'Content-Type': 'application/json', 'Cookie': 'k=abcd1234'})
            out = json.loads(opener.open(req).read())
            self.assertEqual((out['ad']['status'], out['ad']['note']), ('fav', 'ok'))
            self.assertEqual(changes, ['7'])
            stats = json.loads(opener.open(base + '/api/stats?k=abcd1234').read())
            self.assertEqual(stats['status']['fav'], 1)
        finally:
            panel.stop()


CATALOG_STATES = [
    {'filterId': 'filter_enum_make', 'conditions': [], 'values': [{'values': [{'id': 'volkswagen', 'name': 'Volkswagen', 'counter': 9}]}]},
    {'filterId': 'filter_enum_model', 'conditions': [{'filterId': 'filter_enum_make', 'value': 'volkswagen'}],
     'values': [{'values': [{'id': 'golf', 'name': 'Golf', 'counter': 5}]}]},
    {'filterId': 'filter_enum_generation', 'conditions': [{'filterId': 'filter_enum_make', 'value': 'volkswagen'},
                                                          {'filterId': 'filter_enum_model', 'value': 'golf'}],
     'values': [{'values': [{'id': 'gen-vii', 'name': 'VII (2012-2020)'}]}]},
    {'filterId': 'filter_enum_fuel_type', 'conditions': [], 'values': [{'values': [{'id': 'diesel', 'name': 'Diesel'}]}]},
    {'filterId': 'filter_float_price', 'conditions': [], 'values': [{'values': [{'id': '20000', 'name': '20000'}]}]},
]


class FilterTests(unittest.TestCase):
    def test_build_url_like_otomoto(self):
        url = F.build_url({'make': ['volkswagen'], 'model': ['golf'], 'fuel_type': ['diesel', 'petrol'], 'price': {'from': '20 000', 'to': 50000},
                           'year': {'from': 2015}, 'city': 'Nowy Sącz', 'dist': '50', 'no_accident': True, 'bogus': 1})
        self.assertTrue(url.startswith('https://www.otomoto.pl/osobowe/nowy-sacz?'))
        for part in ('search%5Bfilter_enum_make%5D=volkswagen', 'search%5Bfilter_enum_fuel_type%5D%5B0%5D=diesel',
                     'search%5Bfilter_enum_fuel_type%5D%5B1%5D=petrol', 'search%5Bfilter_float_price%3Afrom%5D=20000',
                     'search%5Bfilter_float_price%3Ato%5D=50000', 'search%5Bfilter_float_year%3Afrom%5D=2015', 'search%5Bdist%5D=50',
                     'search%5Bfilter_enum_no_accident%5D=1', 'search%5Border%5D=created_at_first%3Adesc'):
            self.assertIn(part, url)
        self.assertNotIn('bogus', url)
        # promień bez miasta nic nie znaczy; odwrócony zakres jest poprawiany
        self.assertNotIn('dist', F.build_url({'dist': '50'}))
        self.assertEqual(F.clean({'price': {'from': 9, 'to': 1}})['price'], {'from': 1, 'to': 9})
        self.assertEqual(F.build_url({}), 'https://www.otomoto.pl/osobowe?search%5Border%5D=created_at_first%3Adesc')

    def test_catalog_and_summary(self):
        data = {'filters': {'states': CATALOG_STATES}}
        html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(
            {'props': {'pageProps': {'urqlState': {'x': {'data': json.dumps(data)}}}}}) + '</script>'
        catalog = F.catalog_from_html(html)
        self.assertEqual(catalog['makes'], [{'id': 'volkswagen', 'name': 'Volkswagen', 'count': 9}])
        self.assertEqual(catalog['models']['volkswagen'][0]['name'], 'Golf')
        self.assertEqual(catalog['generations']['volkswagen/golf'][0]['id'], 'gen-vii')
        self.assertEqual(catalog['ranges']['price'], [20000])
        text = F.summary({'make': ['volkswagen'], 'model': ['golf'], 'fuel_type': ['diesel'], 'price': {'to': 50000}, 'city': 'Kraków', 'dist': '25'}, catalog)
        self.assertEqual(text, 'Volkswagen Golf · Diesel · Cena do 50 000 zł · Kraków +25 km')
        self.assertEqual(F.summary({}), 'Wszystkie osobowe')
        name = F.short_name({'make': ['volkswagen'], 'model': ['golf'], 'fuel_type': ['diesel'], 'price': {'to': 50000},
                             'year': {'from': 2015}, 'city': 'Kraków', 'dist': '50'}, catalog, limit=45)
        self.assertEqual(name, 'Volkswagen Golf · Diesel · Cena do 50 000 zł')

    def test_shipped_catalog_is_complete(self):
        catalog = json.loads((Path(__file__).parent.parent / 'catalog.json').read_text(encoding='utf-8'))
        self.assertGreater(len(catalog['makes']), 150)
        self.assertIn('golf', [m['id'] for m in catalog['models']['volkswagen']])
        self.assertIn('diesel', [v['id'] for v in catalog['enums']['fuel_type']])


class SavedSearchTests(unittest.TestCase):
    def test_store_crud_and_rename_moves_ads(self):
        store = Store(':memory:')
        sid = store.save_search('Golf', {'make': ['volkswagen']}, 'https://www.otomoto.pl/osobowe?x')
        store.upsert(ad(1), 'Golf')
        with self.assertRaises(ValueError):
            store.save_search('Golf', {}, 'u')
        store.save_search('Golf VII', {'make': ['volkswagen']}, 'u2', enabled=False, search_id=sid)
        self.assertEqual(store.get('1')['search'], 'Golf VII')
        self.assertEqual(store.searches(enabled_only=True), [])
        self.assertTrue(store.delete_search(sid))
        self.assertEqual(store.searches(), [])

    def test_watcher_uses_panel_searches(self):
        ctx = FakeCtx()
        store = Store(':memory:')
        store.save_search('Golf', {}, S.normalize_search_url(URL))
        calls = []
        w = Watcher(ctx, Config(searches=[]), store, fetch=lambda u: calls.append(u) or page([ad(1)]), sleep_between=lambda: True)
        self.assertEqual([n for n, _ in w.active_searches()], ['Golf'])
        w.run_check()
        self.assertEqual(store.get('1')['search'], 'Golf')

    def test_panel_search_api(self):
        store = Store(':memory:')
        woke = []
        fetched = []
        panel = Panel(store, 'abcd1234', port=0, lan=False, on_searches=lambda: woke.append(1),
                      fetch=lambda u: fetched.append(u) or page([ad(5)], total=77), catalog=lambda: {})
        panel.start()
        base = f'http://127.0.0.1:{panel.server.server_address[1]}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(method, path, body=None):
            req = urllib.request.Request(base + path + ('&' if '?' in path else '?') + 'k=abcd1234', method=method,
                                         data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
            return json.loads(opener.open(req).read())
        try:
            preview = call('POST', '/api/preview', {'filters': {'make': ['volkswagen'], 'city': 'Atlantyda'}})
            self.assertEqual(preview['total'], 77)
            self.assertIn('nie rozpoznało miasta', preview['warning'])
            self.assertIn('filter_enum_make', fetched[0])
            created = call('POST', '/api/searches', {'name': 'Golf', 'filters': {'make': ['volkswagen']}})
            listed = call('GET', '/api/searches')['searches']
            self.assertEqual((listed[0]['name'], listed[0]['summary'], listed[0]['enabled']), ('Golf', 'volkswagen', True))
            self.assertIn('filter_enum_make%5D=volkswagen', listed[0]['url'])
            call('POST', '/api/searches', {'id': created['id'], 'name': 'Golf 2', 'filters': {}, 'enabled': False})
            self.assertEqual(store.searches()[0]['name'], 'Golf 2')
            with self.assertRaises(urllib.error.HTTPError) as err:
                call('POST', '/api/searches', {'name': '', 'filters': {}, 'id': 999})
            self.assertEqual(err.exception.code, 400)
            call('DELETE', f"/api/searches/{created['id']}")
            self.assertEqual(store.searches(), [])
            self.assertEqual(len(woke), 3)
        finally:
            panel.stop()


if __name__ == '__main__':
    unittest.main()
