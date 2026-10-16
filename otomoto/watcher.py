"""Pętla łapacza: co kilka minut przegląda wyszukiwania (kilka stron, od najnowszych) i odkłada nowe ogłoszenia."""
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import random
import threading
import time
import webbrowser

from . import filters as F
from . import site as S
from .panel import Panel, load_token
from .store import Store

log = logging.getLogger('otomoto')

MAX_SINGLE_NOTIFICATIONS = 5     # więcej nowych naraz → jedno zbiorcze powiadomienie
REQUEST_GAP = (1.5, 3.5)         # s między zapytaniami do otomoto
MAX_BACKOFF = 30 * 60
CATALOG_MAX_AGE = 7 * 24 * 3600
SHIPPED_CATALOG = Path(__file__).resolve().parent.parent / 'catalog.json'
HELP = ('Komendy (kanał <twój kanał>-ctl, „cmd otomoto-lapacz …”):\n'
        'nowe — nowe auta na telefon · ulubione — ulubione · status — podsumowanie\n'
        'pause / resume — wstrzymaj / wznów · fav|seen|reject <id> — oznacz ogłoszenie')
COMMANDS = {'fav': 'fav', 'ulubione': 'fav', 'seen': 'seen', 'widzialem': 'seen', 'widziałem': 'seen',
            'reject': 'rejected', 'odrzuc': 'rejected', 'odrzuć': 'rejected', 'new': 'new'}


@dataclass
class Config:
    searches: list
    interval: int = 5
    pages: int = 3
    notify_new: bool = True
    notify_price: bool = True
    port: int = 8765
    lan: bool = True
    open_panel: bool = False
    min_drop: int = 500
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_settings(cls, s):
        interval = int(s.get('interval') or 5)
        if not 2 <= interval <= 180:
            raise ValueError('Częstotliwość sprawdzania: 2–180 minut.')
        pages = int(s.get('pages') or 3)
        if not 1 <= pages <= 10:
            raise ValueError('Liczba stron: 1–10.')
        port = int(s.get('port') or 8765)
        if not 1024 <= port <= 65535:
            raise ValueError('Port panelu: 1024–65535.')
        flag = lambda k, d: d if s.get(k) is None else bool(s.get(k))
        return cls(searches=S.parse_searches(s.get('searches')), interval=interval, pages=pages,
                   notify_new=flag('notify_new', True), notify_price=flag('notify_price', True), port=port,
                   lan=flag('lan', True), open_panel=flag('open_panel', False), min_drop=int(s.get('min_drop') or 500))


def describe(ad):
    """„2015 · 242 620 km · 1598 cm³ 132 KM · Benzyna · Manualna”."""
    fmt = lambda n: f'{n:,}'.replace(',', ' ')
    parts = [ad.get('year') and str(ad['year']), ad.get('mileage') is not None and f"{fmt(ad['mileage'])} km",
             ' '.join(p for p in (ad.get('engine_cc') and f"{fmt(ad['engine_cc'])} cm³", ad.get('power_hp') and f"{ad['power_hp']} KM") if p),
             ad.get('fuel'), ad.get('gearbox')]
    return ' · '.join(p for p in parts if p)


def full_title(ad):
    """Tytuł z wersją, bez powtórzeń (otomoto często ma wersję już w tytule)."""
    title, version = ad.get('title') or '', ad.get('version') or ''
    return f'{title} {version}' if version and version.lower() not in title.lower() else title


def price_text(price, currency='PLN'):
    if price is None:
        return 'cena ?'
    return f"{price:,} {'zł' if currency == 'PLN' else currency}".replace(',', ' ')


class Watcher:
    def __init__(self, ctx, cfg, store, fetch=S.fetch, sleep_between=None):
        self.ctx = ctx
        self.cfg = cfg
        self.store = store
        self.fetch = fetch
        self.sleep_between = sleep_between or (lambda: ctx.sleep(random.uniform(*REQUEST_GAP)))
        self.check = 0
        self.failures = 0
        self.paused = False
        self.panel_urls = (None, None)
        self.last_ok = None
        self.wake = threading.Event()     # zmiana wyszukiwań w panelu → sprawdź od razu

    def active_searches(self):
        """Wyszukiwania z panelu (włączone) + linki z ustawień aplikacji."""
        return [(s['name'], s['url']) for s in self.store.searches(enabled_only=True)] + list(self.cfg.searches)

    # --- powiadomienia ---
    def notify_new(self, ad, search):
        title = f"{full_title(ad)} — {price_text(ad.get('price'), ad.get('currency'))}"
        lines = [describe(ad), ', '.join(p for p in (ad.get('city'), ad.get('region')) if p), f'[{search}]']
        self.ctx.notify(title[:120], '\n'.join(l for l in lines if l), event=f"ad-{ad['id']}", priority=4, click=ad.get('url'),
                        image=ad.get('thumb'), stop=False,
                        actions=[('⭐ Ulubione', f"fav {ad['id']}"), ('👀 Widziałem', f"seen {ad['id']}"), ('✖ Odrzuć', f"reject {ad['id']}")])

    def notify_summary(self, items):
        names = ', '.join(sorted({s for _, s in items}))
        text = '\n'.join(f"• {ad['title']} — {price_text(ad.get('price'))}, {ad.get('year') or '?'}" for ad, _ in items[:8])
        if len(items) > 8:
            text += f'\n… i {len(items) - 8} więcej'
        self.ctx.notify(f'{len(items)} nowych ogłoszeń ({names})'[:120], text, event='summary', priority=4,
                        click=self.panel_urls[1] or self.panel_urls[0], stop=False)

    def notify_price(self, ad, old, search):
        self.ctx.notify(f"Obniżka: {ad['title']} — {price_text(ad['price'])}"[:120],
                        f"Było {price_text(old)}, jest {price_text(ad['price'])} (−{price_text(old - ad['price'])}).\n{describe(ad)}\n[{search}]",
                        event=f"drop-{ad['id']}", priority=3, click=ad.get('url'), image=ad.get('thumb'), stop=False,
                        actions=[('⭐ Ulubione', f"fav {ad['id']}"), ('✖ Odrzuć', f"reject {ad['id']}")])

    # --- komendy z telefonu ---
    def handle_commands(self):
        for text in self.ctx.poll_commands():
            words = text.strip().split()
            if not words:
                continue
            word = words[0].lower()
            if word in ('pause', 'wstrzymaj'):
                self.paused = True
                log.info('TELEFON — sprawdzanie wstrzymane.')
            elif word in ('resume', 'wznow', 'wznów'):
                self.paused = False
                log.info('TELEFON — sprawdzanie wznowione.')
            elif word in ('nowe', 'new', 'lista') and len(words) == 1:
                self.send_list('new', 'Nowe')
            elif word in ('ulubione', 'fav') and len(words) == 1:
                self.send_list('fav', 'Ulubione')
            elif word in ('status', 'stan') and len(words) == 1:
                self.send_status()
            elif word in ('pomoc', 'help') and len(words) == 1:
                self.ctx.notify('Łapacz — komendy', HELP, event='help', priority=2, stop=False)
            elif word in COMMANDS and len(words) == 2:
                if self.store.set_status(words[1], COMMANDS[word]):
                    ad = self.store.get(words[1])
                    log.info('TELEFON — %s: %s (%s).', {'fav': 'ulubione', 'seen': 'widziane', 'rejected': 'odrzucone', 'new': 'nowe'}[COMMANDS[word]],
                             ad['title'], price_text(ad.get('price')))
                else:
                    log.warning('TELEFON — nie znam ogłoszenia %s.', words[1])
            else:
                log.warning('TELEFON — nieznana komenda „%s”. %s', text, HELP.replace('\n', ' '))

    def send_list(self, status, label, limit=5):
        """Ogłoszenia z panelu wysłane na telefon (działa też poza domem — przez ntfy)."""
        ads = self.store.query(status=status, sort='found', limit=limit)
        total = self.store.counts()['status'][status]
        log.info('TELEFON — wysyłam listę „%s” (%s z %s).', label, len(ads), total)
        if not ads:
            self.ctx.notify(f'{label}: brak ogłoszeń', 'Nic tu teraz nie ma.', event=f'list-{status}', priority=2, stop=False)
            return
        for ad in reversed(ads):
            actions = [('👀 Widziałem', f"seen {ad['id']}"), ('✖ Odrzuć', f"reject {ad['id']}")]
            if status != 'fav':
                actions.insert(0, ('⭐ Ulubione', f"fav {ad['id']}"))
            self.ctx.notify(f"{full_title(ad)} — {price_text(ad.get('price'), ad.get('currency'))}"[:120],
                            '\n'.join(p for p in (describe(ad), ', '.join(x for x in (ad.get('city'), ad.get('region')) if x), f"[{ad['search']}]") if p),
                            event=f"list-{ad['id']}", priority=3, click=ad.get('url'), image=ad.get('thumb'), stop=False, actions=actions)
        if total > len(ads):
            self.ctx.notify(f'{label}: jeszcze {total - len(ads)}', 'Pozostałe zobaczysz w panelu (w domu) albo po oznaczeniu tych.',
                            event=f'list-more-{status}', priority=2, stop=False)

    def send_status(self):
        counts = self.store.counts()['status']
        lines = [f"Nowe: {counts['new']} · Ulubione: {counts['fav']} · Widziane: {counts['seen']} · Odrzucone: {counts['rejected']}",
                 f'Wyszukiwania: {len(self.active_searches())} · sprawdzanie co {self.cfg.interval} min',
                 f"Ostatnie sprawdzenie: {self.last_ok or 'jeszcze nie'}" + (' · WSTRZYMANY' if self.paused else '')]
        self.ctx.notify('Łapacz Otomoto — status', '\n'.join(lines), event='status', priority=2, stop=False,
                        actions=[('Pokaż nowe', 'nowe'), ('Wznów' if self.paused else 'Wstrzymaj', 'resume' if self.paused else 'pause')])

    # --- jedno sprawdzenie ---
    def run_check(self):
        self.check += 1
        started = time.monotonic()
        fresh, drops, pages = [], [], 0
        for name, url in self.active_searches():
            baseline = self.store.get_meta(f'baseline:{url}') is None
            seen_here = 0
            for page in range(1, self.cfg.pages + 1):
                if self.ctx.stopping:
                    return
                if pages:
                    self.sleep_between()
                html = self.fetch(S.page_url(url, page))
                pages += 1
                total, ads = S.parse(html)
                for ad in ads:
                    change, old = self.store.upsert(ad, name, baseline=baseline)
                    seen_here += 1
                    if change == 'new' and not baseline:
                        fresh.append((ad, name))
                    elif change == 'price_drop' and old - ad['price'] >= self.cfg.min_drop:
                        drops.append((ad, old, name))
                if len(ads) < 32 or page * 32 >= total:
                    break
            if baseline:
                self.store.set_meta(f'baseline:{url}', time.time())
                log.info('[%s] pierwszy przegląd: zapisano %s ogłoszeń jako znane (bez powiadomień). Od teraz łapię nowe.', name, seen_here)
        for ad, name in fresh:
            log.info('NOWE [%s] %s — %s | %s | %s', name, full_title(ad), price_text(ad.get('price')),
                     describe(ad), ad.get('url'))
        for ad, old, name in drops:
            log.info('OBNIŻKA [%s] %s: %s → %s | %s', name, ad['title'], price_text(old), price_text(ad['price']), ad.get('url'))
        if self.cfg.notify_new and fresh:
            if len(fresh) <= MAX_SINGLE_NOTIFICATIONS:
                for ad, name in fresh:
                    self.notify_new(ad, name)
            else:
                self.notify_summary(fresh)
        if self.cfg.notify_price:
            for ad, old, name in drops[:MAX_SINGLE_NOTIFICATIONS]:
                self.notify_price(ad, old, name)
        self.failures = 0
        self.last_ok = time.strftime('%H:%M')
        log.info('[Sprawdzenie #%s] %s stron, nowych: %s, obniżek: %s (%.1f s).', self.check, pages, len(fresh), len(drops),
                 time.monotonic() - started)
        return fresh

    def status(self, text):
        counts = self.store.counts()['status']
        fields = {'Nowe': counts['new'], 'Ulubione': counts['fav'], 'Wyszukiwania': len(self.active_searches())}
        if self.last_ok:
            fields['Ostatnie sprawdzenie'] = self.last_ok
        if self.panel_urls[0]:
            fields['Panel (komputer)'] = self.panel_urls[0]
        if self.panel_urls[1]:
            fields['Panel (telefon, ta sama sieć Wi-Fi)'] = self.panel_urls[1]
        self.ctx.status(text, **fields)

    def loop(self):
        while not self.ctx.stopping:
            self.handle_commands()
            self.wake.clear()
            if not self.paused and not self.active_searches():
                self.status('Dodaj wyszukiwanie w panelu (zakładka „Wyszukiwania”)')
            elif not self.paused:
                self.status(f'Sprawdzam… (#{self.check + 1})')
                try:
                    self.run_check()
                except S.Blocked as exc:
                    self.failures += 1
                    log.warning('Otomoto ogranicza zapytania (%s) — odczekam dłużej.', exc)
                except Exception as exc:
                    self.failures += 1
                    log.error('Błąd sprawdzania: %s', str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__)
                    if self.failures == 3:
                        self.ctx.notify('Łapacz Otomoto — problem', f'Trzy sprawdzenia z rzędu nieudane: {exc}'[:300], event='error',
                                        priority=4, cooldown=3600)
            delay = min(self.cfg.interval * 60 * (2 ** min(self.failures, 4)), MAX_BACKOFF) if self.failures else self.cfg.interval * 60
            self.status('Wstrzymany z telefonu' if self.paused else
                        f'Czekam — następne sprawdzenie o {time.strftime("%H:%M", time.localtime(time.time() + delay))}')
            end = time.monotonic() + delay
            while not self.ctx.stopping and time.monotonic() < end and not self.wake.is_set():
                self.handle_commands()
                if not self.ctx.sleep(min(5, max(0.1, end - time.monotonic()))):
                    break


def load_catalog(data_dir, fetch=S.fetch):
    """Katalog filtrów: świeży z otomoto (raz na tydzień), w razie problemu ostatni zapisany albo dołączony do skryptu."""
    cached = Path(data_dir) / 'katalog.json'
    try:
        if time.time() - cached.stat().st_mtime < CATALOG_MAX_AGE:
            return json.loads(cached.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    try:
        catalog = F.catalog_from_html(fetch(F.BASE))
        cached.write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
        log.info('Katalog filtrów otomoto odświeżony: %s marek.', len(catalog['makes']))
        return catalog
    except Exception as exc:
        log.warning('Nie udało się odświeżyć katalogu filtrów (%s) — używam zapisanego.', str(exc)[:120])
    for path in (cached, SHIPPED_CATALOG):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
    return {}


def run(ctx):
    cfg = Config.from_settings(ctx.settings)
    data_dir = Path(ctx.data_dir or '.')
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / 'ogloszenia.db')
    watcher = Watcher(ctx, cfg, store)
    catalog = load_catalog(data_dir)
    panel = Panel(store, load_token(data_dir), port=cfg.port, lan=cfg.lan, searches=cfg.searches,
                  catalog=lambda: catalog, on_searches=watcher.wake.set,
                  on_change=lambda ad_id, body: log.info('PANEL — %s: %s', ad_id, ', '.join(f'{k}={v}' for k, v in body.items())[:120]))
    try:
        panel.start()
        watcher.panel_urls = panel.urls()
        log.info('PANEL na komputerze: %s', watcher.panel_urls[0])
        # Aplikacja pokaże panel w oknie skryptu (starsze wersje aplikacji pominą tę wiadomość).
        ctx.send({'type': 'panel', 'url': watcher.panel_urls[0], 'label': 'Wyszukiwania i ogłoszenia'})
        if watcher.panel_urls[1]:
            log.info('PANEL na telefonie (ta sama sieć Wi-Fi): %s', watcher.panel_urls[1])
        if cfg.open_panel:
            webbrowser.open(watcher.panel_urls[0])
    except OSError as exc:
        log.warning('Nie udało się uruchomić panelu na porcie %s (%s) — zmień port w ustawieniach. Łapanie działa dalej.', cfg.port, exc)
    count = len(watcher.active_searches())
    log.info('Łapacz: %s wyszukiwań, co %s min, %s stron(y) każdego (od najnowszych).', count, cfg.interval, cfg.pages)
    if not count:
        log.warning('Brak wyszukiwań — dodaj je w panelu (zakładka „Wyszukiwania”). Łapacz zacznie od razu po zapisaniu.')
    ctx.notify('Łapacz Otomoto działa', f'{count} wyszukiwań, sprawdzanie co {cfg.interval} min.', event='started',
               click=watcher.panel_urls[1] or None)
    try:
        watcher.loop()
    finally:
        panel.stop()
