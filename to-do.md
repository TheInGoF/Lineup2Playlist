# To-Do — offene Punkte aus der Gegenprüfung

Stand 2026-07-05. Alle Major-Funde sind gefixt; das hier sind die
verbliebenen kleineren Punkte (Fundstellen aus dem Multi-Agent-Review).

## festival_cli.py

- [x] **plexapi-Vorab-Check vor der Sammelphase** — erledigt: `run_generation`
  versucht bei Ziel Plex (kein Dry-Run) `import plexapi` vor dem Login.
- [x] **Dry-Run mit Ziel Plex nicht blockieren** — erledigt: Plex-Check nur
  bei `target == "plex" and not dry_run`.
- [x] **Teilweise gefüllte Aufgabenliste bei Abbruch während der Sammelphase**
  — erledigt: `finally` schreibt jetzt auch, wenn `tasks.has_tasks()`
  (Abbruch mitten in `collect()`), mit Test.

## Tests

- [x] **build_plex_playlist getestet** — erledigt: direkter Treffer +
  Exakt-Bevorzugung, Compilation-Fallback, kein Treffer → `not_matched`.
- [x] **412-Retry + Playlist-Wiederverwendung getestet** — erledigt
  (siehe unten, Bug-Fix).
- [x] **CLI-Fixes getestet** — erledigt: Env-Vars schlagen Config, Config 0600,
  Verzeichnis als Line-Up verworfen, Dry-Run+Plex blockt nicht,
  Aufgabenliste überlebt Build-Fehler.
- [x] Test für unbekannte `###`-Header-Warnung in `parse_lineup` — erledigt.
- [ ] Test für Token-Recache nach Auto-Refresh (`tidal_login`: geänderter
  `access_token` beim Cache-Login → Datei wird neu geschrieben). (offen,
  niedrige Prio — Code ist umgesetzt und läuft)

## Web-Oberfläche ✅ UMGESETZT

- [x] **Grafische Nutzeroberfläche im Browser**, aus dem Terminal startbar:
  `python festival_cli.py -w` (Standard-Port 666), `-w PORT` für andere
  Ports. Modul: `festival_web.py` (nur Stdlib `http.server`, bindet an
  `127.0.0.1`). Getestet in `test_festival_web.py`.
  - [x] Gleiche Funktionen wie das Terminal-Menü: Line-Up wählen,
    Ziel/Modus/Songs/Name/Dry-Run, Vorschau, Start mit Live-Fortschritt
    über Server-Sent Events, Anzeige der manuellen Aufgaben.
  - [x] Tidal-OAuth-Login-Link erscheint klickbar im Live-Log der Web-UI.
  - [x] Port-666-Bindung: klare Fehlermeldung wenn privilegiert
    (macOS/Linux brauchen dort `sudo`), Hinweis auf `-w 6660`.
  - Offen (nice-to-have): Line-Up per Datei-Upload statt nur Auswahl;
    Abbrechen-Button für laufende Läufe.

## Nice-to-have

- [ ] Echter Ende-zu-Ende-Lauf gegen Tidal (interaktiver OAuth-Login nötig,
  konnte hier nicht automatisiert werden): `festival_cli.py` → Dry-Run mit
  `rodeo.txt`, prüfen wie viele der 43 Bands sauber aufgelöst werden und
  wie oft der neue Top-Hit-Fallback greift (Aufgabenliste ansehen).
- [ ] `.gitignore` anlegen, falls das Projekt ein Git-Repo wird
  (`.venv/`, `tidal_session.json`, `festival_cli_config.json`,
  `manuelle_aufgaben.txt`, `__pycache__/`, `.pytest_cache/`).
- [ ] VS-Code-Bug melden (`/bug`): Extension biegt `/workflows` auf
  `/__remote-workflow` um, das lokal nicht funktioniert.
