# Łapacz Otomoto — skrypt dla ScriptPlatformFB

Pilnuje Twoich wyszukiwań na **otomoto.pl** i odkłada każde nowe ogłoszenie (marka, model, cena, rok, przebieg,
silnik, paliwo, skrzynia, miejsce, zdjęcie, link) do panelu, w którym oznaczasz: ⭐ ulubione, 👀 widziałem, ✖ odrzucam
i dopisujesz notatki. Działa na komputerze i na telefonie.

## Dlaczego wyszukiwarka „gubi” nowe auta

Otomoto sortuje „od najnowszych” po dacie **utworzenia** ogłoszenia, a nie po dacie publikacji. Ogłoszenie, które
czekało na moderację albo płatność, pojawia się od razu kilka pozycji (czy stron) niżej — pod autami dodanymi później.
Skrypt przegląda kilka stron i pamięta numery wszystkich widzianych ogłoszeń, więc łapie także takie „spóźnione”.

## Jak używać

1. Na otomoto.pl ustaw filtry (marka, model, rocznik, cena, przebieg, paliwo…) i skopiuj adres strony z wynikami.
2. W ScriptPlatformFB: **+ Dodaj skrypt** → to repozytorium → **Zainstaluj**.
3. W polu **Wyszukiwania** wklej linki — jeden w linii, opcjonalnie z nazwą: `Golf diesel | https://www.otomoto.pl/...`.
4. **Start**. Pierwszy przegląd zapisuje obecne ogłoszenia jako znane (bez powiadomień), potem co kilka minut łapie nowe.

## Telefon i komputer

- **Panel** otwiera się w przeglądarce na komputerze. Adres dla telefonu (ta sama sieć Wi-Fi) jest na kafelku i w logu,
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
`otomoto/site.py` — linki i odczyt wyników, `store.py` — baza SQLite, `panel.py` + `panel.html` — panel WWW,
`watcher.py` — pętla, powiadomienia, komendy. Bez zewnętrznych bibliotek (tylko standardowy Python).
