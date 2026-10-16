import json
from pathlib import Path
import tempfile
import unittest
import urllib.request

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
        with self.assertRaises(ValueError):
            S.parse_searches('')
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
        for bad in ({'interval': 1}, {'pages': 11}, {'searches': ''}):
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


if __name__ == '__main__':
    unittest.main()
