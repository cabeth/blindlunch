# Bereich Statistik Blind Lunch

Minimaler Dash-Prototyp für Anmeldung, zufällige Lunch-Teams und die Zuweisung einer organisierenden Person.

## Lokal starten

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Danach <http://127.0.0.1:8050> öffnen.

Ohne Posit-Header läuft die App als Benutzer `default`. Für lokale Tests kann die Rolle beim Start gesetzt werden:

```bash
# Admin-Modus
BLIND_LUNCH_LOCAL_USER=cakol python app.py

# Beispiel für den normalen Modus
BLIND_LUNCH_LOCAL_USER=fazuf python app.py
```

Testdaten werden nur auf ausdrücklichen Wunsch in eine neue Datenbank geschrieben:

```bash
BLIND_LUNCH_SEED_TEST_DATA=1 BLIND_LUNCH_LOCAL_USER=cakol python app.py
```

## Posit Connect

Die App stellt `server = app.server` für Posit Connect bereit und liest den Benutzer aus dem Header `RStudio-Connect-Credentials`. `CAKOL` erhält automatisch den Admin-Modus; Groß-/Kleinschreibung spielt keine Rolle.

Setze `BLIND_LUNCH_DB` in Posit auf den absoluten Pfad einer persistenten, für die App beschreibbaren SQLite-Datei. Ohne diese Einstellung liegt die Datenbank im App-Verzeichnis und kann bei einem erneuten Deployment ersetzt werden. Bei SQLite sollte die App mit nur einem Python-Prozess betrieben werden.
