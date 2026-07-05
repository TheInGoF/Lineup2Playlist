#!/usr/bin/env python3
"""
Lineup2Playlist - interactive CLI
=================================

Menu-driven front end for festival_playlist.py: choose a line-up file, set
target/mode/options, preview and build the playlist - without having to
remember command-line flags.

Start:
    python festival_cli.py            # interactive menu (terminal)
    python festival_cli.py lineup.txt # preselect a line-up
    python festival_cli.py -w         # graphical web interface (port 666)
    python festival_cli.py -w 6660    # web interface on a different port

Settings are remembered in festival_cli_config.json.
For scripting/automation, festival_playlist.py with its flags
(--target, --lineup, ...) remains usable unchanged.
"""

import glob
import json
import os
import sys

import festival_playlist as fp

# Anchored to the script directory so the config is always the same,
# independent of the working directory (like TASK_FILE/SESSION_FILE in core).
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "festival_cli_config.json")

# ---------------------------------------------------------------------------
# ANSI COLORS (auto-off when not a terminal or NO_COLOR is set)
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
# CONFIGURATION (persistent)
# ---------------------------------------------------------------------------

DEFAULTS = {
    "lineup": None,
    "target": "tidal",           # tidal | plex
    "catalog": False,            # False = top tracks, True = catalog
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
    # Environment variables override the saved config
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
        # 0600: the config may hold the Plex token in clear text
        fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.chmod(CONFIG_FILE, 0o600)
    except OSError as e:
        print(yellow(f"  ! Could not save configuration: {e}"))


# ---------------------------------------------------------------------------
# FIND / LOAD LINE-UP FILES
# ---------------------------------------------------------------------------

def is_lineup_file(path):
    """True if the .txt contains a '### Line-Up'/'### Bands' header."""
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
    """Look for line-up .txt files in the project folder."""
    base = os.path.dirname(os.path.abspath(__file__))
    hits = []
    for p in sorted(glob.glob(os.path.join(base, "*.txt"))):
        if os.path.basename(p) == os.path.basename(fp.TASK_FILE):
            continue
        if is_lineup_file(p):
            hits.append(p)
    return hits


def lineup_stats(path):
    """Load (genres, bands) without exiting the program on error."""
    try:
        return fp.parse_lineup(path, verbose=False)
    except SystemExit as e:
        print(red(f"  ! {e}"))
        return None, None


# ---------------------------------------------------------------------------
# INPUT HELPERS
# ---------------------------------------------------------------------------

def ask(prompt, default=None):
    """input() with a default and a clean abort on Ctrl-C/Ctrl-D."""
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
        print(yellow(f"  Please enter a number between {lo} and {hi}."))


def pause():
    try:
        input(dim("\n  <Enter> to return to the menu "))
    except (EOFError, KeyboardInterrupt):
        print()


# ---------------------------------------------------------------------------
# MENU ACTIONS
# ---------------------------------------------------------------------------

def choose_lineup(cfg):
    files = find_lineup_files()
    print()
    if files:
        print(bold("  Line-up files found:"))
        for i, p in enumerate(files, 1):
            genres, bands = lineup_stats(p)
            info = f"{len(bands)} bands, {len(genres)} genres" if bands else "unreadable"
            print(f"   [{i}] {os.path.basename(p)}  {dim('(' + info + ')')}")
        print(f"   [p] enter a different path")
        sel = ask("Choice", "1")
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
            print(yellow("  Invalid choice."))
            return
    else:
        print(yellow("  No line-up files found in the project folder."))

    path = ask("Path to the line-up .txt")
    if not path:
        return
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        print(red(f"  Not a file: {path}"))
        return
    cfg["lineup"] = path
    _suggest_name(cfg)


def _suggest_name(cfg):
    """Suggest a playlist name from the file name while the default is set."""
    if cfg["name"] == DEFAULTS["name"]:
        stem = os.path.splitext(os.path.basename(cfg["lineup"]))[0]
        cfg["name"] = f"{stem.replace('_', ' ').replace('-', ' ').title()} - Best Of"


def preview_lineup(cfg):
    if not cfg["lineup"]:
        print(yellow("\n  Please choose a line-up file first."))
        return
    genres, bands = lineup_stats(cfg["lineup"])
    if not bands:
        return
    print()
    print(bold(f"  {os.path.basename(cfg['lineup'])}"))
    print(f"  Genre priority: {cyan(', '.join(genres) if genres else '-')}")
    print(f"  {len(bands)} bands:")
    width = max(len(b) for b in bands) + 3
    cols = max(1, 78 // width)
    for row in range(0, len(bands), cols):
        line = "".join(b.ljust(width) for b in bands[row:row + cols])
        print(f"    {line.rstrip()}")
    pause()


def edit_plex(cfg):
    print()
    print(bold("  Plex settings") + dim("  (can also be set via env vars "
          "PLEX_BASEURL/PLEX_TOKEN/PLEX_LIBRARY)"))
    url = ask("Server URL", cfg["plex_baseurl"])
    if url is None:
        return
    token = ask("Token", cfg["plex_token"])
    if token is None:
        return
    lib = ask("Music library", cfg["plex_library"])
    if lib is None:
        return
    cfg.update(plex_baseurl=url, plex_token=token, plex_library=lib)


def plex_ready(cfg):
    return ("YOUR_PLEX_TOKEN" not in cfg["plex_token"]
            and "192.168.x.x" not in cfg["plex_baseurl"])


def show_tasks():
    print()
    if not os.path.exists(fp.TASK_FILE):
        print(dim("  No task list yet (created after the first run)."))
    else:
        with open(fp.TASK_FILE, encoding="utf-8") as f:
            for line in f:
                print("  " + line.rstrip())
    pause()


def run_generation(cfg):
    if not cfg["lineup"]:
        print(yellow("\n  Please choose a line-up file first."))
        return
    # Only check Plex credentials and package when actually building against
    # Plex (in a dry run build_plex_playlist is never called).
    if cfg["target"] == "plex" and not cfg["dry_run"]:
        if not plex_ready(cfg):
            print(yellow("\n  Plex is selected as the target, but the server "
                         "URL/token are still placeholders.\n  Please fill in "
                         "the Plex settings first (menu item 8)."))
            return
        try:
            import plexapi  # noqa: F401
        except ImportError:
            print(yellow("\n  The 'plexapi' package is not installed: "
                         "pip install plexapi"))
            return

    print()
    print(bold("  Ready to go:"))
    print(f"    Line-up   {os.path.basename(cfg['lineup'])}")
    print(f"    Target    {cfg['target']}")
    print(f"    Mode      {'Catalog (all albums)' if cfg['catalog'] else 'Top tracks'}")
    print(f"    Playlist  {cfg['name']}" + (dim("  (dry run: NOT created)")
                                            if cfg["dry_run"] else ""))
    ok = ask("Start? (y/n)", "y")
    if ok is None or ok.lower() not in ("y", "yes"):
        print(dim("  Cancelled."))
        return

    # Pass the Plex config through to the core module
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
            print(dim("Dry run: no playlist created."))
        elif not collected:
            print(yellow("No tracks collected - no playlist created."))
        elif cfg["target"] == "tidal":
            fp.build_tidal_playlist(session, collected, cfg["name"], cfg["catalog"])
        else:
            fp.build_plex_playlist(collected, cfg["name"], tasks)
    except SystemExit as e:
        print(red(f"\n  Aborted: {e}"))
    except KeyboardInterrupt:
        print(red("\n  Cancelled by user."))
    except Exception as e:
        print(red(f"\n  Error: {type(e).__name__}: {e}"))
    finally:
        # Always write the task list once anything was collected - even if
        # building the playlist fails OR collect() itself aborts
        if collected is not None or tasks.has_tasks():
            tasks.write(fp.TASK_FILE)
            if tasks.has_tasks():
                print(yellow(f"  {tasks.count()} open manual task(s) - "
                             "menu item [a] shows them."))
            else:
                print(green("  All done automatically - no open tasks."))
    pause()


# ---------------------------------------------------------------------------
# MAIN MENU
# ---------------------------------------------------------------------------

def print_menu(cfg):
    print()
    print(cyan("  ============================================="))
    print(cyan("   LINEUP2PLAYLIST") + dim("  (TIDAL/Plex)"))
    print(cyan("  ============================================="))

    if cfg["lineup"]:
        genres, bands = None, None
        try:
            genres, bands = fp.parse_lineup(cfg["lineup"], verbose=False)
        except SystemExit:
            pass
        info = (f"{os.path.basename(cfg['lineup'])}  "
                + dim(f"({len(bands)} bands, {len(genres)} genres)")
                if bands else red(os.path.basename(cfg["lineup"]) + "  (unreadable!)"))
    else:
        info = yellow("none selected yet")

    target = "TIDAL playlist" if cfg["target"] == "tidal" else "Plex matching"
    if cfg["target"] == "plex" and not plex_ready(cfg):
        target += "  " + yellow("(! credentials missing)")
    mode = "Catalog (all albums)" if cfg["catalog"] else "Top tracks (TIDAL ranking)"

    print(bold("   Configuration"))
    print(f"     Line-up     {info}")
    print(f"     Target      {target}")
    print(f"     Mode        {mode}")
    print(f"     Songs/band  {cfg['top']}")
    print(f"     Playlist    {cfg['name']}")
    print(f"     Dry run     {green('on') if cfg['dry_run'] else dim('off')}")
    print()
    print(bold("   [1]") + " Choose line-up file")
    print(bold("   [2]") + " Switch target (TIDAL <-> Plex)")
    print(bold("   [3]") + " Switch collection mode (top tracks <-> catalog)")
    print(bold("   [4]") + " Change songs per band")
    print(bold("   [5]") + " Change playlist name")
    print(bold("   [6]") + " Toggle dry run")
    print(bold("   [7]") + " Line-up preview")
    print(bold("   [8]") + " Plex settings")
    print(green("   [s]") + " Build playlist (start)")
    print(bold("   [a]") + " Show manual tasks")
    print(bold("   [q]") + " Quit")


def _maybe_launch_web(argv):
    """-w [PORT] -> launch the web interface instead of the terminal menu."""
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

    # Optional argument: preselect a line-up file
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        cand = os.path.expanduser(sys.argv[1])
        if os.path.exists(cand):
            cfg["lineup"] = cand
            _suggest_name(cfg)
        else:
            print(red(f"Line-up file not found: {cand}"))

    # If none selected yet: auto-pick the only file found
    if not cfg["lineup"]:
        files = find_lineup_files()
        if len(files) == 1:
            cfg["lineup"] = files[0]
            _suggest_name(cfg)

    while True:
        print_menu(cfg)
        choice = ask("Choice")
        if choice is None or choice.lower() == "q":
            save_config(cfg)
            print(dim("  Settings saved. See you at the next festival!"))
            return

        c = choice.lower()
        if c == "1":
            choose_lineup(cfg)
        elif c == "2":
            cfg["target"] = "plex" if cfg["target"] == "tidal" else "tidal"
        elif c == "3":
            cfg["catalog"] = not cfg["catalog"]
        elif c == "4":
            n = ask_int("Songs per band", cfg["top"], 1, 50)
            if n is not None:
                cfg["top"] = n
        elif c == "5":
            name = ask("Playlist name", cfg["name"])
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
            print(yellow("  Unknown choice."))


if __name__ == "__main__":
    main()
