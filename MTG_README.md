# MTG/FCI FRP Hotspot Watch

Serverseitige GitHub-Actions-Überwachung für das LSA SAF **MTG FRP Pixel / MTFRPPIXEL (LSA-509)** Produkt.

Offizielle Produktdaten:
- Sensor/Plattform: FCI / MTG
- Produkt: MTFRPPIXEL / LSA-509
- räumliche Auflösung: 1 km
- zeitliche Auflösung: 10 min
- Zeitraum: 2025–NRT

## Installation in deinem bestehenden Repository

Kopiere diese Dateien in das bestehende `firms-hotspot-watch` Repository:

- `mtg_watch.py`
- `mtg-requirements.txt`
- `.github/workflows/mtg-frp-watch.yml`
- `data/mtg_seen.json`
- `data/mtg_status.json`
- `data/mtg_events.jsonl`

## GitHub Secrets

Unter **Settings → Secrets and variables → Actions** anlegen:

- `LSASAF_USERNAME`
- `LSASAF_PASSWORD`

Diese Werte werden nicht in Git gespeichert.

## Zeitplan

Der Workflow läuft nominell alle 10 Minuten, um 7 Minuten gegenüber der vollen Zehner-Minute versetzt:

`07, 17, 27, 37, 47, 57`

Damit soll die Chance erhöht werden, dass ein neues 10-Minuten-MTG-Produkt bereits im NRT-Datendienst angekommen ist.

## Sicherheits-/Datenprinzip

Das Skript:
- verwendet nur tatsächlich vorhandene MTFRPPIXEL-Dateien;
- interpoliert keine Werte;
- akzeptiert nur positive, numerische FRP-Werte;
- filtert danach exakt auf das bestehende Polygon;
- schreibt Fehler in `data/mtg_status.json`, statt unbekannte Produktfelder zu erraten.

## Wichtiger erster Test

MTFRPPIXEL ist aktuell ein Demonstrationsprodukt. LSA SAF dokumentiert HDF5 als Datenformat, aber Produktstrukturen können sich ändern. Deshalb erkennt das Skript Dataset-Namen semantisch.

Nach dem ersten manuellen Workflow-Lauf bitte `data/mtg_status.json` prüfen. Falls `errors` nicht leer ist, enthält die Meldung die tatsächlich im Produkt gefundenen Dataset-Namen. Dann kann der Extraktor gezielt an genau diese reale Datei angepasst werden.

## Ausgaben

- `data/mtg_status.json` – letzter Lauf und neue Treffer
- `data/mtg_events.jsonl` – Historie erstmalig erkannter Treffer
- `data/mtg_seen.json` – Deduplizierungszustand

Attribution: EUMETSAT LSA SAF [MTFRPPIXEL, LSA-509].
