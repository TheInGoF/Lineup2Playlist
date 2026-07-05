#!/usr/bin/env python3
"""
Lineup2Playlist - Interaktive CLI-Oberflaeche
=============================================

Menuegefuehrte Oberflaeche fuer festival_playlist.py: Line-Up-Datei waehlen,
Ziel/Modus/Optionen einstellen, Vorschau ansehen und die Playlist bauen -
ohne sich Kommandozeilen-Flags merken zu muessen.

Start:
    python festival_cli.py            # interaktives Menue (Terminal)
    python festival_cli.py lineup.txt # Line-Up direkt vorwaehlen
    python festival_cli.py -w         # grafische Web-Oberflaeche (Port 666)
    python festival_cli.py -w 6660    # Web-Oberflaeche auf anderem Port

Die Einstellungen werden in festival_cli_config.json gemerkt.
Fuer den Skript-/Automatisierungs-Einsatz bleibt festival_playlist.py
mit seinen Flags (--target, --lineup, ...) unveraendert nutzbar.
"""

import glob
import json
import os
import sys

import festival_playlist as fp

# Am Skriptverzeichnis verankert, damit die Config unabhaengig vom
# Arbeitsverzeichnis immer dieselbe ist (wie TASK_FILE/SESSION_FILE im Kern).
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "festival_cli_config.json")

# ---------------------------------------------------------------------------
# ANSI-FARBEN (automatisch aus, wenn kein Terminal oder NO_COLOR gesetzt)
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else str(text)

def bold(t):   return _c("1", t)
def dim(t):    return _c("2", t)
def cyan(t):   return _c("36", t)
def green(t):  return _c("32", t)
def yellow(t): return _c("33", t)
def red(t):    return _c("31", t)


# ---------------------------------------------------------------------------
# KONFIGURATION (persistent)
# ---------------------------------------------------------------------------

DEFAULTS = {
    "lineup": None,
    "target": "tidal",           # tidal | plex
    "catalog": False,            # False = Top-Tracks, True = Katalog
    "top": fp.TOP_N,
    "name": "Festival - Best Of",
    "dry_run": False,
    "plex_baseurl": os.environ.get("PLEX_BASEURL", fp.PLEX_BASEURL),
    "plex_token": os.environ.get("PLEX_TOKEN", fp.PLEX_TOKEN),
    "plex_library": os.environ.get("PLEX_LIBRARY", fp.PLEX_LIBRARY),
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    # Umgebungsvariablen schlagen die gespeicherte Config
    for key, env in (("plex_baseurl", "PLEX_BASEURL"),
                     ("plex_token", "PLEX_TOKEN"),
                     ("plex_library", "PLEX_LIBRARY")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if cfg["lineup"] and not os.path.isfile(cfg["lineup"]):
        cfg["lineup"] = None
    return cfg


def save_config(cfg):
    try:
        # 0600: Config enthaelt ggf. den Plex-Token im Klartext
        fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.chmod(CONFIG_FILE, 0o600)
    except OSError as e:
        print(yellow(f"  ! Konfiguration nicht gespeichert: {e}"))


# ---------------------------------------------------------------------------
# LINE-UP-DATEIEN FINDEN / LADEN
# ---------------------------------------------------------------------------

def is_lineup_file(path):
    """True, wenn die TXT einen '### Line-Up'/'### Bands'-Header enthaelt."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip().lower()
                if s.startswith("###"):
                    h = s.lstrip("#").strip()
                    if h.startswith("line") or h.startswith("band"):
                        return True
    except OSError:
        pass
    return False


def find_lineup_files():
    """Sucht Line-Up-TXTs im Projektverzeichnis."""
    base = os.path.dirname(os.path.abspath(__file__))
    hits = []
    for p in sorted(glob.glob(os.path.join(base, "*.txt"))):
        if os.path.basename(p) == os.path.basename(fp.TASK_FILE):
            continue
        if is_lineup_file(p):
            hits.append(p)
    return hits


def lineup_stats(path):
    """(genres, bands) laden, ohne bei Fehlern das Programm zu beenden."""
    try:
        return fp.parse_lineup(path, verbose=False)
    except SystemExit as e:
        print(red(f"  ! {e}"))
        return None, None


# ---------------------------------------------------------------------------
# EINGABE-HELFER
# ---------------------------------------------------------------------------

def ask(prompt, default=None):
    """input() mit Default und sauberem Abbruch bei Strg-C/Strg-D."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return val or (str(default) if default is not None else "")


def ask_int(prompt, default, lo=1, hi=100):
    while True:
        val = ask(prompt, default)
        if val is None:
            return None
        try:
            n = int(val)
            if lo <= n <= hi:
                return n
        except ValueError:
            pass
        print(yellow(f"  Bitte Zahl zwischen {lo} und {hi} eingeben."))


def pause():
    try:
        input(dim("\n  <Enter> fuer zurueck zum Menue "))
    except (EOFError, KeyboardInterrupt):
        print()


# ---------------------------------------------------------------------------
# MENUE-AKTIONEN
# ---------------------------------------------------------------------------

def choose_lineup(cfg):
    files = find_lineup_files()
    print()
    if files:
        print(bold("  Gefundene Line-Up-Dateien:"))
        for i, p in enumerate(files, 1):
            genres, bands = lineup_stats(p)
            info = f"{len(bands)} Bands, {len(genres)} Genres" if bands else "unlesbar"
            print(f"   [{i}] {os.path.basename(p)}  {dim('(' + info + ')')}")
        print(f"   [p] anderen Pfad eingeben")
        sel = ask("Auswahl", "1")
        if sel is None:
            return
        if sel.lower() != "p":
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(files):
                    cfg["lineup"] = files[idx]
                    _suggest_name(cfg)
                    return
            except ValueError:
                pass
            print(yellow("  Ungueltige Auswahl."))
            return
    else:
        print(yellow("  Keine Line-Up-Dateien im Projektverzeichnis gefunden."))

    path = ask("Pfad zur Line-Up-TXT")
    if not path:
        return
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        print(red(f"  Keine Datei: {path}"))
        return
    cfg["lineup"] = path
    _suggest_name(cfg)


def _suggest_name(cfg):
    """Playlist-Namen aus dem Dateinamen vorschlagen, solange der Default steht."""
    if cfg["name"] == DEFAULTS["name"]:
        stem = os.path.splitext(os.path.basename(cfg["lineup"]))[0]
        cfg["name"] = f"{stem.replace('_', ' ').replace('-', ' ').title()} - Best Of"


def preview_lineup(cfg):
    if not cfg["lineup"]:
        print(yellow("\n  Bitte zuerst eine Line-Up-Datei waehlen."))
        return
    genres, bands = lineup_stats(cfg["lineup"])
    if not bands:
        return
    print()
    print(bold(f"  {os.path.basename(cfg['lineup'])}"))
    print(f"  Genre-Prioritaet: {cyan(', '.join(genres) if genres else '-')}")
    print(f"  {len(bands)} Bands:")
    width = max(len(b) for b in bands) + 3
    cols = max(1, 78 // width)
    for row in range(0, len(bands), cols):
        line = "".join(b.ljust(width) for b in bands[row:row + cols])
        print(f"    {line.rstrip()}")
    pause()


def edit_plex(cfg):
    print()
    print(bold("  Plex-Einstellungen") + dim("  (auch per Umgebungsvariablen "
          "PLEX_BASEURL/PLEX_TOKEN/PLEX_LIBRARY setzbar)"))
    url = ask("Server-URL", cfg["plex_baseurl"])
    if url is None:
        return
    token = ask("Token", cfg["plex_token"])
    if token is None:
        return
    lib = ask("Musik-Bibliothek", cfg["plex_library"])
    if lib is None:
        return
    cfg.update(plex_baseurl=url, plex_token=token, plex_library=lib)


def plex_ready(cfg):
    return ("DEIN_PLEX_TOKEN" not in cfg["plex_token"]
            and "192.168.x.x" not in cfg["plex_baseurl"])


def show_tasks():
    print()
    if not os.path.exists(fp.TASK_FILE):
        print(dim("  Noch keine Aufgabenliste vorhanden (erst nach einem Lauf)."))
    else:
        with open(fp.TASK_FILE, encoding="utf-8") as f:
            for line in f:
                print("  " + line.rstrip())
    pause()


def run_generation(cfg):
    if not cfg["lineup"]:
        print(yellow("\n  Bitte zuerst eine Line-Up-Datei waehlen."))
        return
    # Plex-Zugangsdaten und -Paket nur pruefen, wenn tatsaechlich gegen Plex
    # gebaut wird (im Dry-Run wird build_plex_playlist nie aufgerufen).
    if cfg["target"] == "plex" and not cfg["dry_run"]:
        if not plex_ready(cfg):
            print(yellow("\n  Plex ist als Ziel gewaehlt, aber Server-URL/Token "
                         "sind noch Platzhalter.\n  Bitte zuerst die Plex-"
                         "Einstellungen ausfuellen (Menuepunkt 8)."))
            return
        try:
            import plexapi  # noqa: F401
        except ImportError:
            print(yellow("\n  Das Paket 'plexapi' ist nicht installiert: "
                         "pip install plexapi"))
            return

    print()
    print(bold("  Los geht's:"))
    print(f"    Line-Up   {os.path.basename(cfg['lineup'])}")
    print(f"    Ziel      {cfg['target']}")
    print(f"    Modus     {'Katalog (alle Alben)' if cfg['catalog'] else 'Top-Tracks'}")
    print(f"    Playlist  {cfg['name']}" + (dim("  (Dry-Run: wird NICHT angelegt)")
                                            if cfg["dry_run"] else ""))
    ok = ask("Starten? (j/n)", "j")
    if ok is None or ok.lower() not in ("j", "ja", "y", "yes"):
        print(dim("  Abgebrochen."))
        return

    # Plex-Konfiguration ans Kernmodul durchreichen
    fp.PLEX_BASEURL = cfg["plex_baseurl"]
    fp.PLEX_TOKEN = cfg["plex_token"]
    fp.PLEX_LIBRARY = cfg["plex_library"]

    print()
    tasks = fp.TaskLog()
    collected = None
    try:
        genres, bands = fp.parse_lineup(cfg["lineup"])
        session = fp.tidal_login()
        collected = fp.collect(session, bands, genres, cfg["top"],
                               cfg["catalog"], tasks)

        if cfg["dry_run"]:
            print(dim("Dry-Run: keine Playlist angelegt."))
        elif not collected:
            print(yellow("Keine Tracks gesammelt - keine Playlist angelegt."))
        elif cfg["target"] == "tidal":
            fp.build_tidal_playlist(session, collected, cfg["name"], cfg["catalog"])
        else:
            fp.build_plex_playlist(collected, cfg["name"], tasks)
    except SystemExit as e:
        print(red(f"\n  Abbruch: {e}"))
    except KeyboardInterrupt:
        print(red("\n  Vom Benutzer abgebrochen."))
    except Exception as e:
        print(red(f"\n  Fehler: {type(e).__name__}: {e}"))
    finally:
        # Aufgabenliste IMMER schreiben, sobald etwas erfasst wurde - auch
        # wenn der Playlist-Bau fehlschlaegt ODER collect() selbst abbricht
        if collected is not None or tasks.has_tasks():
            tasks.write(fp.TASK_FILE)
            if tasks.has_tasks():
                print(yellow(f"  {tasks.count()} offene manuelle Aufgabe(n) - "
                             "Menuepunkt [a] zeigt sie an."))
            else:
                print(green("  Alles automatisch erledigt - keine offenen Aufgaben."))
    pause()


# ---------------------------------------------------------------------------
# HAUPTMENUE
# ---------------------------------------------------------------------------

def print_menu(cfg):
    print()
    print(cyan("  ============================================="))
    print(cyan("   LINEUP2PLAYLIST") + dim("  (Tidal/Plex)"))
    print(cyan("  ============================================="))

    if cfg["lineup"]:
        genres, bands = None, None
        try:
            genres, bands = fp.parse_lineup(cfg["lineup"], verbose=False)
        except SystemExit:
            pass
        info = (f"{os.path.basename(cfg['lineup'])}  "
                + dim(f"({len(bands)} Bands, {len(genres)} Genres)")
                if bands else red(os.path.basename(cfg["lineup"]) + "  (unlesbar!)"))
    else:
        info = yellow("noch keine gewaehlt")

    target = "Tidal-Playlist" if cfg["target"] == "tidal" else "Plex-Matching"
    if cfg["target"] == "plex" and not plex_ready(cfg):
        target += "  " + yellow("(! Zugangsdaten fehlen)")
    mode = "Katalog (alle Alben)" if cfg["catalog"] else "Top-Tracks (Tidal-Ranking)"

    print(bold("   Konfiguration"))
    print(f"     Line-Up     {info}")
    print(f"     Ziel        {target}")
    print(f"     Modus       {mode}")
    print(f"     Songs/Band  {cfg['top']}")
    print(f"     Playlist    {cfg['name']}")
    print(f"     Dry-Run     {green('an') if cfg['dry_run'] else dim('aus')}")
    print()
    print(bold("   [1]") + " Line-Up-Datei waehlen")
    print(bold("   [2]") + " Ziel wechseln (Tidal <-> Plex)")
    print(bold("   [3]") + " Sammel-Modus wechseln (Top-Tracks <-> Katalog)")
    print(bold("   [4]") + " Songs je Band aendern")
    print(bold("   [5]") + " Playlist-Name aendern")
    print(bold("   [6]") + " Dry-Run umschalten")
    print(bold("   [7]") + " Line-Up-Vorschau")
    print(bold("   [8]") + " Plex-Einstellungen")
    print(green("   [s]") + " Playlist bauen (Start)")
    print(bold("   [a]") + " Manuelle Aufgaben anzeigen")
    print(bold("   [q]") + " Beenden")


def _maybe_launch_web(argv):
    """-w [PORT] -> Web-Oberflaeche starten statt Terminal-Menue."""
    if "-w" not in argv and "--web" not in argv:
        return False
    flag = "-w" if "-w" in argv else "--web"
    port = 666
    i = argv.index(flag)
    if i + 1 < len(argv) and argv[i + 1].isdigit():
        port = int(argv[i + 1])
    import festival_web
    festival_web.serve(port)
    return True


def main():
    if _maybe_launch_web(sys.argv[1:]):
        return

    cfg = load_config()

    # Optionales Argument: Line-Up-Datei direkt vorwaehlen
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        cand = os.path.expanduser(sys.argv[1])
        if os.path.exists(cand):
            cfg["lineup"] = cand
            _suggest_name(cfg)
        else:
            print(red(f"Line-Up-Datei nicht gefunden: {cand}"))

    # Falls noch keine gewaehlt: einzige gefundene Datei automatisch nehmen
    if not cfg["lineup"]:
        files = find_lineup_files()
        if len(files) == 1:
            cfg["lineup"] = files[0]
            _suggest_name(cfg)

    while True:
        print_menu(cfg)
        choice = ask("Auswahl")
        if choice is None or choice.lower() == "q":
            save_config(cfg)
            print(dim("  Einstellungen gespeichert. Bis zum naechsten Festival!"))
            return

        c = choice.lower()
        if c == "1":
            choose_lineup(cfg)
        elif c == "2":
            cfg["target"] = "plex" if cfg["target"] == "tidal" else "tidal"
        elif c == "3":
            cfg["catalog"] = not cfg["catalog"]
        elif c == "4":
            n = ask_int("Songs je Band", cfg["top"], 1, 50)
            if n is not None:
                cfg["top"] = n
        elif c == "5":
            name = ask("Playlist-Name", cfg["name"])
            if name:
                cfg["name"] = name
        elif c == "6":
            cfg["dry_run"] = not cfg["dry_run"]
        elif c == "7":
            preview_lineup(cfg)
        elif c == "8":
            edit_plex(cfg)
        elif c == "s":
            save_config(cfg)
            run_generation(cfg)
        elif c == "a":
            show_tasks()
        elif c == "":
            continue
        else:
            print(yellow("  Unbekannte Auswahl."))


if __name__ == "__main__":
    main()
