# Łapacz Otomoto — skrypt dla ScriptPlatformFB

Pilnuje Twoich wyszukiwań na **otomoto.pl** i odkłada każde nowe ogłoszenie (marka, model, cena, rok, przebieg,
silnik, paliwo, skrzynia, miejsce, zdjęcie, link) do panelu, w którym oznaczasz: ⭐ ulubione, 👀 widziałem, ✖ odrzucam
i dopisujesz notatki. Działa na komputerze i na telefonie.

## Dlaczego wyszukiwarka „gubi” nowe auta

Otomoto sortuje „od najnowszych” po dacie **utworzenia** ogłoszenia, a nie po dacie publikacji. Ogłoszenie, które
czekało na moderację albo płatność, pojawia się od razu kilka pozycji (czy stron) niżej — pod autami dodanymi później.
Skrypt przegląda kilka stron i pamięta numery wszystkich widzianych ogłoszeń, więc łapie także takie „spóźnione”.

## Jak używać

1. W ScriptPlatformFB: **+ Dodaj skrypt** → to repozytorium → **Zainstaluj** → **Start**. Panel pojawi się w oknie skryptu
   (na górze, nad ustawieniami; starsze wersje aplikacji — w przeglądarce, adres jest w logu).
2. W panelu: **🔎 Wyszukiwania → + Nowe wyszukiwanie**. Filtry jak na otomoto: marka (kilka naraz), model, generacja,
   nadwozie, cena, rok, przebieg, paliwo, skrzynia, stan, lokalizacja z promieniem, a w „Więcej filtrów”: pojemność, moc,
   napęd, uszkodzenia, sprzedający, kraj pochodzenia, drzwi, kolor, bezwypadkowy, pierwszy właściciel, ASO.
   Na dole od razu widać, ile ogłoszeń pasuje (i kilka zdjęć). **Zapisz i śledź**.
3. Dodaj tyle wyszukiwań, ile chcesz. Każde możesz edytować, skopiować, wyłączyć albo usunąć — z komputera i z telefonu.
4. Nowe wyszukiwanie: pierwszy przegląd zapisuje obecne ogłoszenia jako znane (bez powiadomień), potem łapacz łapie nowe.

Zamiast formularza możesz też wkleić gotowe linki z otomoto w ustawieniach skryptu w aplikacji.

## Telefon i komputer

- **Panel** jest w oknie skryptu w aplikacji (przycisk „Otwórz w przeglądarce” otwiera go osobno). Adres dla telefonu (ta sama sieć Wi-Fi) jest na kafelku i w logu,
  np. `http://192.168.1.20:8765/?k=…`. Za pierwszym razem Windows zapyta o zaporę dla Pythona — zezwól dla sieci prywatnej.
  Na telefonie dodaj stronę do ekranu głównego, będzie jak aplikacja.
- **Powiadomienia ntfy** (gdziekolwiek jesteś): zdjęcie, cena, rok, przebieg, silnik; kliknięcie otwiera ogłoszenie,
  przyciski **⭐ Ulubione / 👀 Widziałem / ✖ Odrzuć** zapisują wybór w panelu. Przy kilku nowych naraz — jedno zbiorcze.
  Obniżki cen (od ustawionej kwoty) też przychodzą jako powiadomienie.
- Komendy na kanał `<twój kanał>-ctl`: `cmd otomoto-lapacz pause`, `… resume`, `… fav <id>`, `… seen <id>`, `… reject <id>`.

Dane są w folderze danych skryptu (`ogloszenia.db`). Klucz panelu: `panel-klucz.txt` (usuń plik, żeby wygenerować nowy).

## Rozwój

```
python -m unittest discover -s tests -t .
```
`otomoto/site.py` — linki i odczyt wyników, `filters.py` — filtry, katalog marek/modeli i budowanie adresu, `store.py` — baza SQLite, `panel.py` + `panel.html` — panel WWW,
`watcher.py` — pętla, powiadomienia, komendy. Bez zewnętrznych bibliotek (tylko standardowy Python).
