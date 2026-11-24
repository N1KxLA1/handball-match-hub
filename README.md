# Handball Match-Hub

Spiele, Tabellen und Spielerstatistiken der QHL, NLB, des Mobiliar Handball Cups, der SPL1 und SPL2 sowie der Schweizer Teams in der EHF European League und im European Cup.

- Daten: öffentliche Schnittstelle des [handball.ch Matchcenters](https://www.handball.ch/de/matchcenter/) und eurohandball.com
- Aktualisierung: GitHub Actions alle 15 Minuten (`.github/workflows/update.yml`); Spielerfotos und Logos einmal pro Tag
- Anzeige: GitHub Pages (`index.html`), die Daten liegen nicht im Repository, sondern werden bei jedem Lauf frisch erzeugt

Ein Lauf von Hand: Reiter **Actions** → „Match-Hub aktualisieren“ → **Run workflow**.

Lokal testen:

```bash
pip install pillow
mkdir -p _site && cp index.html _site/
python3 fetch_hub.py _site/data --portraits --logos
python3 fetch_hub.py _site/data --ehf-portraits-only
python3 -m http.server -d _site 8000
```
