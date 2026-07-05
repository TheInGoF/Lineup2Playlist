#!/usr/bin/env python3
"""
Lineup2Playlist - Kernmodul (Tidal-Quelle)
==========================================

Generisches Tool: Line-Up und Genre-Prioritaeten kommen aus einer TXT-Datei.
Funktioniert fuer jedes Festival, nicht nur Ruhrpott Rodeo.

TXT-Format (siehe example_lineup.txt):
    ### Genres
    Punk, Rock, Alternative, Hip-Hop
    ### Line-Up
    Die Toten Hosen
    The Baboon Show
    ...
  - Genres: kommagetrennt, ABSTEIGEND priorisiert (erstes = hoechste Prioritaet).
  - Line-Up: eine Band pro Zeile. Duplikate werden automatisch entfernt.
  - Leere Zeilen und Kommentarzeilen ('#' + Leerzeichen oder alleinstehendes
    '#') werden ignoriert. Bandnamen wie '#1 Hit' bleiben dadurch erhalten.

Sammel-Modi:
  Standard   -> Artist.get_top_tracks(limit)   (Tidal-Ranking, recency-biased)
  --catalog  -> meistgespielte Songs quer ueber ALLE Alben (echte Bandhistorie)

Mehrdeutige Bandnamen:
  Liefert die Suche nur einen klaren Treffer -> direkt genommen.
  Gibt es mehrere Kandidaten, gewinnt ein exakter Namens-Match.
  Danach wuerde die Genre-Prioritaetsliste entscheiden - die Tidal-API
  (tidalapi 0.8.x) liefert allerdings keine Genre-Daten an Artist/Album.
  In dem Fall wird der Tidal-Top-Hit genommen und die Band zur Kontrolle
  in der Aufgabenliste vermerkt ("bitte pruefen").

Ziel-Backend:
  --target tidal  -> legt direkt eine Tidal-Playlist an
  --target plex   -> matcht die Tracks gegen deine Plex-Bibliothek

Abhaengigkeiten:
  pip install "tidalapi>=0.8" plexapi
"""

import argparse
import sys
import time
import os
import json
from datetime import datetime

import tidalapi

# ---------------------------------------------------------------------------
# KONFIGURATION
# ---------------------------------------------------------------------------

TOP_N = 10

# Am Skriptverzeichnis verankert, damit Session-Cache und Aufgabenliste
# unabhaengig vom Arbeitsverzeichnis immer dieselben Dateien sind.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_FILE = os.path.join(BASE_DIR, "manuelle_aufgaben.txt")
SESSION_FILE = os.path.join(BASE_DIR, "tidal_session.json")

PLEX_BASEURL = "http://192.168.x.x:32400"
PLEX_TOKEN = "DEIN_PLEX_TOKEN"
PLEX_LIBRARY = "Musik"


def _plex_ist_platzhalter():
    return "DEIN_PLEX_TOKEN" in PLEX_TOKEN or "192.168.x.x" in PLEX_BASEURL


# ---------------------------------------------------------------------------
# TXT-PARSER
# ---------------------------------------------------------------------------

def _ist_kommentar(line):
    """Kommentar = '#' + Leerzeichen oder alleinstehendes '#'.
    So bleiben Bandnamen wie '#1 Hit' erhalten."""
    return line == "#" or line.startswith("# ")


def parse_lineup(path, verbose=True):
    """
    Liest lineup.txt und gibt (genres, bands) zurueck.
    genres: Liste lowercase, in Prioritaetsreihenfolge (absteigend).
    bands:  Liste der Bandnamen (Original-Schreibweise), dedupliziert.
    """
    if not os.path.exists(path):
        sys.exit(f"Line-Up-Datei nicht gefunden: {path}")

    genres, bands = [], []
    seen_bands = set()
    duplikate = 0
    streuner = 0
    unbekannte = {}          # unbekannter '###'-Header -> ignorierte Zeilen
    section = None
    unbekannt_header = None

    try:
        # utf-8-sig: BOM am Dateianfang wuerde sonst den ersten Header verstecken
        f = open(path, encoding="utf-8-sig", errors="replace")
    except OSError as e:
        sys.exit(f"Line-Up-Datei nicht lesbar: {path} ({e})")
    with f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            # Sektions-Header erkennen
            if line.startswith("###"):
                header = line.lstrip("#").strip().lower()
                unbekannt_header = None
                if header.startswith("genre"):
                    section = "genres"
                elif header.startswith("line") or header.startswith("band"):
                    section = "bands"
                else:
                    section = None
                    unbekannt_header = line
                continue

            if _ist_kommentar(line):
                continue

            if section == "genres":
                for g in line.split(","):
                    g = g.strip().lower()
                    if g and g not in genres:
                        genres.append(g)
            elif section == "bands":
                key = line.lower()
                if key in seen_bands:
                    duplikate += 1
                    continue
                seen_bands.add(key)
                bands.append(line)
            elif unbekannt_header:
                unbekannte[unbekannt_header] = unbekannte.get(unbekannt_header, 0) + 1
            else:
                streuner += 1

    if not bands:
        sys.exit("Keine Bands im Line-Up gefunden. Steht unter '### Line-Up' etwas?")
    if verbose:
        if streuner:
            print(f"  ! {streuner} Zeile(n) vor dem ersten '###'-Header ignoriert.")
        for kopf, n in unbekannte.items():
            print(f"  ! {n} Zeile(n) unter unbekanntem Header '{kopf}' ignoriert.")
        if duplikate:
            print(f"  ! {duplikate} doppelte Bandzeile(n) entfernt.")
        print(f"Line-Up geladen: {len(bands)} Bands, {len(genres)} Genres "
              f"(Prioritaet: {', '.join(genres) if genres else '-'})\n")
    return genres, bands


# ---------------------------------------------------------------------------
# MANUELLE AUFGABENLISTE
# ---------------------------------------------------------------------------

class TaskLog:
    def __init__(self):
        self.not_found_artist = []
        self.genre_reject = []
        self.uncertain = []
        self.no_tracks = []
        self.errors = []
        self.not_matched = []

    def _alle(self):
        return (self.not_found_artist + self.genre_reject + self.uncertain
                + self.no_tracks + self.errors + self.not_matched)

    def count(self):
        return len(self._alle())

    def has_tasks(self):
        return bool(self._alle())

    def write(self, path):
        lines = ["# Festival Playlist - Manuelle Aufgaben",
                 "# Vom Script NICHT automatisch erledigt. Bitte von Hand pruefen.",
                 ""]
        if self.not_found_artist:
            lines.append("## Bands bei Tidal NICHT gefunden (Name/Schreibweise pruefen):")
            lines += [f"  - {b}" for b in self.not_found_artist]
            lines.append("")
        if self.genre_reject:
            lines.append("## Mehrdeutig - kein Kandidat mit passendem Genre:")
            lines += [f"  - {b}" for b in self.genre_reject]
            lines.append("")
        if self.uncertain:
            lines.append("## Mehrdeutig - automatisch per Top-Hit gewaehlt (bitte pruefen):")
            lines += [f"  - {b}" for b in self.uncertain]
            lines.append("")
        if self.no_tracks:
            lines.append("## Bands ohne Tracks (evtl. nicht bei Tidal verfuegbar):")
            lines += [f"  - {b}" for b in self.no_tracks]
            lines.append("")
        if self.errors:
            lines.append("## API-Fehler bei der Abfrage (spaeter erneut versuchen):")
            lines += [f"  - {b}" for b in self.errors]
            lines.append("")
        if self.not_matched:
            lines.append("## Songs nicht in Plex-Bibliothek gefunden (manuell ergaenzen):")
            lines += [f"  - {s}" for s in self.not_matched]
            lines.append("")
        if not self.has_tasks():
            lines.append("Alles automatisch erledigt - keine offenen Aufgaben.")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\nAufgabenliste geschrieben: {path}")


# ---------------------------------------------------------------------------
# TIDAL-LOGIN (mit Session-Cache)
# ---------------------------------------------------------------------------

def _expiry_laden(value):
    """Gecachten expiry_time-String zurueck in datetime wandeln (oder None)."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value


def _expiry_speichern(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value is not None else None


def _session_cachen(session, still=False):
    try:
        # 0600: Datei enthaelt Access-/Refresh-Token im Klartext
        fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expiry_time": _expiry_speichern(session.expiry_time),
            }, f)
        os.chmod(SESSION_FILE, 0o600)
        if not still:
            print(f"Tidal-Session gecacht: {SESSION_FILE}")
    except Exception as e:
        print(f"  ! Session konnte nicht gecacht werden: {e}", file=sys.stderr)


def tidal_login():
    session = tidalapi.Session()
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE) as f:
                d = json.load(f)
            ok = session.load_oauth_session(
                d["token_type"], d["access_token"],
                d.get("refresh_token"), _expiry_laden(d.get("expiry_time")),
            )
            if ok and session.check_login():
                print("Tidal-Session aus Cache geladen.")
                # tidalapi refresht abgelaufene Tokens beim Laden automatisch
                # -> erneuerten Token-Satz zurueckschreiben, sonst macht
                # jeder weitere Lauf denselben Refresh-Roundtrip erneut
                if session.access_token != d.get("access_token"):
                    _session_cachen(session, still=True)
                return session
        except Exception:
            pass

    try:
        session.login_oauth_simple()
    except TimeoutError:
        sys.exit("Tidal-Login abgelaufen: Der Link wurde nicht rechtzeitig "
                 "bestaetigt. Bitte erneut starten.")
    if not session.check_login():
        sys.exit("Tidal-Login fehlgeschlagen.")

    _session_cachen(session)
    return session


# ---------------------------------------------------------------------------
# GENRE-ERMITTLUNG
# ---------------------------------------------------------------------------

def artist_genre_tags(artist):
    """
    Sammelt Genre-Hinweise vom Artist-Objekt (lowercase-Menge).

    Hinweis: tidalapi 0.8.x setzt an Artist/Album KEINE Genre-Attribute,
    die Menge ist dort also immer leer. Die Attribut-Pruefung bleibt fuer
    kuenftige Bibliotheksversionen erhalten (kostet keine API-Calls).
    """
    tags = set()
    for attr in ("genre", "genres"):
        val = getattr(artist, attr, None)
        if isinstance(val, str):
            tags.add(val.lower())
        elif isinstance(val, (list, tuple)):
            tags.update(str(x).lower() for x in val)
    return tags


def genre_priority(artist, priority_list):
    """
    Gibt (index, tags) zurueck: index (0 = hoechste Prio) des besten
    passenden Genres oder None, wenn kein Listen-Genre passt; tags ist
    die Menge der gefundenen Genre-Hinweise.
    """
    tags = artist_genre_tags(artist)
    best = None
    for tag in tags:
        for idx, g in enumerate(priority_list):
            if g in tag:
                if best is None or idx < best:
                    best = idx
    return best, tags


# ---------------------------------------------------------------------------
# ARTIST FINDEN (Genre nur bei Mehrdeutigkeit)
# ---------------------------------------------------------------------------

# Ab wie vielen ernsthaften Treffern gilt eine Suche als "mehrdeutig".
AMBIGUOUS_THRESHOLD = 2

def find_artist(session, name, priority_list):
    """
    Rueckgabe: (artist|None, reason) mit reason in
    {"ok", "fallback", "not_found", "genre_reject"}.
    - Nur ein Treffer / eindeutiger exakter Namens-Match -> direkt ("ok").
    - Mehrere Kandidaten -> Genre-Prioritaet entscheidet, sofern die API
      Genre-Daten liefert. Liefert sie keine (tidalapi 0.8.x) oder gibt es
      keine Prioritaetsliste, wird der Tidal-Top-Hit (falls unter den
      Kandidaten) bzw. der erste Kandidat genommen und als "fallback"
      markiert -> landet zur Kontrolle in der Aufgabenliste.
    """
    res = session.search(name, models=[tidalapi.artist.Artist], limit=5)
    artists = res.get("artists") or []
    if not artists:
        return None, "not_found"

    # Eindeutig: nur ein Treffer -> direkt nehmen
    if len(artists) < AMBIGUOUS_THRESHOLD:
        return artists[0], "ok"

    # Exakter Namens-Match unter den Kandidaten? Dann ist es nicht wirklich
    # mehrdeutig - der exakte Treffer gewinnt (haeufigster Normalfall).
    exact = [a for a in artists if (a.name or "").strip().lower() == name.strip().lower()]
    if len(exact) == 1:
        return exact[0], "ok"

    candidates = list(exact) if exact else list(artists)

    # Genre-Prioritaet anwenden, sofern die API ueberhaupt Genres liefert
    if priority_list:
        tagged = [(cand, genre_priority(cand, priority_list)) for cand in candidates]
        if any(tags for _, (_, tags) in tagged):
            best_artist, best_idx = None, None
            for cand, (idx, _) in tagged:
                if idx is not None and (best_idx is None or idx < best_idx):
                    best_artist, best_idx = cand, idx
            if best_artist is not None:
                print(f"     [Genre-Disambig] '{name}' -> {best_artist.name} "
                      f"(Prio-Genre: {priority_list[best_idx]})")
                return best_artist, "ok"
            return None, "genre_reject"

    # Keine Genre-Daten verfuegbar oder keine Prioritaetsliste
    # -> Tidal-Top-Hit, aber nur wenn er unter den Kandidaten ist
    # (candidates ist ggf. schon auf exakte Namens-Treffer eingegrenzt)
    top = res.get("top_hit")
    chosen = candidates[0]
    if isinstance(top, tidalapi.artist.Artist):
        for cand in candidates:
            if getattr(cand, "id", None) == getattr(top, "id", None):
                chosen = cand
                break
    print(f"     [Top-Hit-Fallback] '{name}' -> {chosen.name} (mehrdeutig, "
          f"bitte pruefen)")
    return chosen, "fallback"


# ---------------------------------------------------------------------------
# SAMMEL-MODI
# ---------------------------------------------------------------------------

def tracks_top(artist, limit):
    return artist.get_top_tracks(limit)


def tracks_catalog(artist, limit):
    try:
        albums = artist.get_albums()
    except Exception:
        albums = []
    # get_ep_singles ist die aktuelle Methode, get_albums_ep_singles der
    # deprecatete Fallback fuer aeltere tidalapi-Versionen.
    for getter in ("get_ep_singles", "get_albums_ep_singles"):
        fn = getattr(artist, getter, None)
        if callable(fn):
            try:
                albums = (albums or []) + (fn() or [])
            except Exception:
                pass
            break

    pool = {}
    for alb in albums or []:
        try:
            for t in alb.tracks():
                key = (t.name or "").strip().lower()
                if not key:
                    continue
                pop = getattr(t, "popularity", 0) or 0
                cur = pool.get(key)
                if cur is None or (getattr(cur, "popularity", 0) or 0) < pop:
                    pool[key] = t
        except Exception:
            continue
        time.sleep(0.05)

    ranked = sorted(pool.values(),
                    key=lambda t: getattr(t, "popularity", 0) or 0,
                    reverse=True)
    return ranked[:limit]


def collect(session, bands, priority_list, limit, catalog, tasks, pause=0.2):
    collected = []
    seen_track_ids = set()
    mode = "Katalog" if catalog else "Top-Tracks"
    print(f"Sammel-Modus: {mode}, {limit} Songs je Band\n")

    for band in bands:
        try:
            artist, reason = find_artist(session, band, priority_list)
        except Exception as e:
            print(f"-> {band}: API-Fehler bei der Suche ({e})")
            tasks.errors.append(f"{band} (Suche: {e})")
            time.sleep(pause)
            continue

        if artist is None:
            if reason == "genre_reject":
                print(f"-> {band}: mehrdeutig, kein Genre-Match -> manuelle Aufgabe")
                tasks.genre_reject.append(band)
            else:
                print(f"-> {band}: NICHT gefunden")
                tasks.not_found_artist.append(band)
            time.sleep(pause)
            continue

        if reason == "fallback":
            tasks.uncertain.append(f"{band} -> {artist.name}")

        try:
            tracks = tracks_catalog(artist, limit) if catalog else tracks_top(artist, limit)
        except Exception as e:
            print(f"-> {band}: Fehler beim Laden der Tracks ({e})")
            tasks.errors.append(f"{band} (Tracks: {e})")
            time.sleep(pause)
            continue

        if not tracks:
            print(f"-> {band}: keine Tracks")
            tasks.no_tracks.append(band)
            time.sleep(pause)
            continue

        print(f"-> {artist.name}")
        for t in tracks:
            tid = getattr(t, "id", None)
            if tid is not None and tid in seen_track_ids:
                print(f"     {t.name}  (Duplikat, uebersprungen)")
                continue
            if tid is not None:
                seen_track_ids.add(tid)
            pop = getattr(t, "popularity", None)
            suffix = f"  (pop {pop})" if pop is not None else ""
            print(f"     {t.name}{suffix}")
            collected.append((band, t))
        time.sleep(pause)

    print(f"\nGesamt: {len(collected)} Tracks gesammelt.\n")
    return collected


# ---------------------------------------------------------------------------
# ZIELE
# ---------------------------------------------------------------------------

def _http_status(exc):
    """HTTP-Statuscode aus einer requests-Exception ziehen (oder None)."""
    return getattr(getattr(exc, "response", None), "status_code", None)


def _ist_rate_limit(exc):
    """429 erkennen - auch tidalapis TooManyRequests (hat kein .response)."""
    return _http_status(exc) == 429 or type(exc).__name__ == "TooManyRequests"


def _existing_playlist(session, name):
    """Bestehende UserPlaylist mit exakt diesem Namen finden (oder None)."""
    try:
        for p in session.user.playlists():
            if (getattr(p, "name", "") or "").strip() == name.strip():
                return p
    except Exception:
        pass
    return None


def _playlist_track_ids(playlist):
    """Alle Track-IDs einer Playlist paginiert auslesen (Tidal begrenzt ~100)."""
    ids = set()
    offset, PAGE = 0, 100
    while True:
        try:
            batch = playlist.tracks(limit=PAGE, offset=offset) or []
        except Exception:
            break
        for t in batch:
            tid = getattr(t, "id", None)
            if tid is not None:
                ids.add(tid)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return ids


def _add_batch_with_retry(session, playlist, ids, retries=3, pause=1.0):
    """
    Einen Block hinzufuegen, mit Retry bei zwei bekannten Transient-Fehlern:
      - 412 (veralteter ETag): Playlist-Objekt frisch laden, dann erneut.
      - 429 / TooManyRequests (Rate-Limit): mit Backoff erneut versuchen.
    Andere Fehler werden sofort weitergereicht. Rueckgabe: (playlist, erfolg);
    playlist kann durch ein frisch geladenes Objekt ersetzt worden sein.
    """
    last_exc = None
    for attempt in range(retries):
        try:
            playlist.add(ids)
            return playlist, True
        except Exception as e:
            last_exc = e
            if not (_http_status(e) == 412 or _ist_rate_limit(e)):
                raise  # nicht behebbar -> nach oben
            if attempt < retries - 1:
                if _http_status(e) == 412:
                    try:
                        playlist = session.playlist(playlist.id)  # frischer ETag
                    except Exception:
                        try:
                            playlist._reparse()
                        except Exception:
                            pass
                time.sleep(pause * (attempt + 1))  # Backoff (v.a. fuer 429)
    if last_exc is not None:
        print(f"  ! Block nach {retries} Versuchen aufgegeben: {last_exc}",
              file=sys.stderr)
    return playlist, False


def build_tidal_playlist(session, collected, name, catalog):
    if not collected:
        print("Keine Tracks gesammelt - keine Tidal-Playlist angelegt.")
        return
    desc = ("Meistgespielte Songs quer ueber alle Alben" if catalog
            else "Populaerste Songs (Tidal-Ranking)")

    # In Sammelreihenfolge dedupliziert (Tidal ueberspringt Dubletten still)
    track_ids = list(dict.fromkeys(t.id for _, t in collected))

    # Bestehende Playlist gleichen Namens wiederverwenden -> Wiederaufnahme
    # nach einem Abbruch, OHNE bereits vorhandene Tracks doppelt einzuspeisen.
    playlist = _existing_playlist(session, name)
    if playlist is not None:
        vorhanden = _playlist_track_ids(playlist)
        fehlend = [tid for tid in track_ids if tid not in vorhanden]
        print(f"Bestehende Playlist '{name}' gefunden ({len(vorhanden)} Tracks)"
              f" - ergaenze {len(fehlend)} fehlende.")
    else:
        playlist = session.user.create_playlist(name, desc)
        vorhanden = set()
        fehlend = track_ids

    if not fehlend:
        print(f"Playlist '{name}' ist bereits vollstaendig ({len(vorhanden)} Tracks).")
        return

    BATCH = 50
    hinzugefuegt = 0
    for i in range(0, len(fehlend), BATCH):
        block = fehlend[i:i + BATCH]
        try:
            playlist, ok = _add_batch_with_retry(session, playlist, block)
        except Exception as e:
            print(f"  ! Fehler beim Hinzufuegen ab Track {i + 1}: {e}",
                  file=sys.stderr)
            ok = False
        if not ok:
            print(f"  ! Block ab Track {i + 1} nicht hinzugefuegt. Ein erneuter "
                  f"Programmlauf ergaenzt die restlichen Tracks (ohne Duplikate).")
            break
        hinzugefuegt += len(block)
        time.sleep(0.3)  # kurze Pause -> ETag-Konflikt gar nicht erst provozieren

    print(f"Tidal-Playlist '{name}': {hinzugefuegt} neu hinzugefuegt, "
          f"insgesamt {len(vorhanden) + hinzugefuegt} Tracks.")


def build_plex_playlist(collected, name, tasks):
    from plexapi.server import PlexServer

    plex = PlexServer(PLEX_BASEURL, PLEX_TOKEN)
    music = plex.library.section(PLEX_LIBRARY)

    def _beste_treffer(hits, title):
        """Exakten Titel-Treffer bevorzugen (searchTracks matcht Substrings)."""
        exakt = [h for h in hits
                 if (h.title or "").strip().lower() == title.strip().lower()]
        return exakt[0] if exakt else hits[0]

    matched = []
    for band, t in collected:
        artist_name = t.artist.name if getattr(t, "artist", None) else band
        title = t.name
        hits = music.searchTracks(title=title, filters={"artist.title": artist_name})
        if not hits:
            # Fallback: auch Track-Artist (originalTitle) pruefen, damit
            # Compilation-Tracks gefunden werden (dort ist der Album-Artist
            # z.B. 'Various Artists').
            wanted = artist_name.lower()
            hits = [h for h in music.searchTracks(title=title)
                    if wanted in (h.grandparentTitle or "").lower()
                    or wanted in (getattr(h, "originalTitle", "") or "").lower()]
        if hits:
            matched.append(_beste_treffer(hits, title))
        else:
            tasks.not_matched.append(f"{artist_name} - {title}")

    if matched:
        plex.createPlaylist(name, items=matched)
        print(f"Plex-Playlist '{name}' angelegt: {len(matched)} Tracks.")
    else:
        print("Keine Tracks in der Plex-Bibliothek gefunden - keine Playlist angelegt.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Lineup2Playlist (Tidal/Plex)")
    ap.add_argument("--target", choices=["tidal", "plex"], required=True)
    ap.add_argument("--lineup", required=True,
                    help="Pfad zur Line-Up-TXT (Pflicht, z.B. example_lineup.txt)")
    ap.add_argument("--top", type=int, default=TOP_N,
                    help=f"Songs je Band (Default {TOP_N})")
    ap.add_argument("--catalog", action="store_true",
                    help="meistgespielte Songs quer ueber ALLE Alben statt nur Top-Ranking")
    ap.add_argument("--name", default="Festival - Best Of")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur sammeln + Aufgabenliste, keine Playlist anlegen")
    args = ap.parse_args()

    # Plex-Vorabpruefung VOR der (langen) Sammelphase, nicht danach
    if args.target == "plex" and not args.dry_run:
        try:
            import plexapi  # noqa: F401
        except ImportError:
            sys.exit("Das Paket 'plexapi' ist nicht installiert: pip install plexapi")
        if _plex_ist_platzhalter():
            sys.exit("PLEX_BASEURL/PLEX_TOKEN sind noch Platzhalter - bitte oben "
                     "im Skript eintragen (oder die interaktive CLI "
                     "festival_cli.py nutzen).")

    priority_list, bands = parse_lineup(args.lineup)

    tasks = TaskLog()
    session = tidal_login()

    collected = collect(session, bands, priority_list, args.top, args.catalog, tasks)

    build_error = None
    if args.dry_run:
        print("Dry-Run: keine Playlist angelegt.")
    else:
        try:
            if args.target == "tidal":
                build_tidal_playlist(session, collected, args.name, args.catalog)
            else:
                build_plex_playlist(collected, args.name, tasks)
        except Exception as e:
            build_error = e
            print(f"FEHLER beim Anlegen der Playlist: {type(e).__name__}: {e}",
                  file=sys.stderr)

    # Aufgabenliste IMMER schreiben, auch wenn der Playlist-Bau scheitert
    tasks.write(TASK_FILE)
    if tasks.has_tasks():
        print("Es gibt offene manuelle Aufgaben - siehe Datei oben.")

    if build_error is not None:
        sys.exit(1)


if __name__ == "__main__":
    main()
