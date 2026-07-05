# Lineup2Playlist

<p align="center">
  <img src="images/logo.svg" alt="Lineup2Playlist logo" width="120">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776ab" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/UI-Web%20%C2%B7%20CLI-19b6ff" alt="UI: Web and CLI">
  <img src="https://img.shields.io/badge/sources-TIDAL%20%C2%B7%20Plex-fc6719" alt="Sources: Tidal and Plex">
  <img src="https://img.shields.io/badge/tests-101%20passing-2fd07a" alt="Tests: 101 passing">
</p>

*Turn a festival line-up into a ready-to-play playlist — one text file of band
names in, a full best-of playlist out, either created directly on **TIDAL** or
matched against your own **Plex** library.*

> Personal hobby project. Provided as-is, with no warranties or support guarantees.

> **Not affiliated with, endorsed by, or connected to TIDAL or Plex.** "TIDAL"
> and "Plex" are named only descriptively. No brand logos or assets are used.

## Quickstart (TL;DR)

1. **Install:** create a virtualenv and `pip install -r requirements.txt` (see [Install](#install)).
2. **Write a line-up:** copy [`example_lineup.txt`](example_lineup.txt) to `my_festival.txt` — genres on top, one band per line.
3. **Start the web UI:** `python festival_cli.py -w 6660`, then open <http://localhost:6660>.
4. Pick the line-up, choose **Tidal** or **Plex**, tick **Dry-Run** for a first test, hit **Build**.
5. On the first run a **TIDAL login link appears in the live log** — click it, confirm in TIDAL, and it continues automatically.

## What is it for?

You have a festival line-up and want a playlist of each band's best songs,
without adding forty artists by hand. Drop the line-up into a text file and the
tool looks up every band, collects their top tracks, and builds one playlist —
on TIDAL directly, or matched track-by-track against a local Plex library.

**Why this**

- **One text file in, a whole playlist out** — no manual searching, no copy-paste.
- **Two collection modes:** each band's TIDAL top tracks, or their most-played
  songs across *all* albums (`--catalog`) for a deeper best-of.
- **Two targets:** create a TIDAL playlist, or match everything against **Plex**.
- **Resumable & duplicate-safe:** a re-run reuses the same playlist and only adds
  what's missing, so an interrupted upload just continues.
- **Nothing is added blindly** — anything ambiguous or unmatched lands in a
  `manual_tasks.txt` to-do list for you to check.
- **Standalone:** pure Python standard library for the UI (only `tidalapi` and
  `plexapi` as real dependencies); settings live in a local file, not a cloud account.

## Install

```bash
cd "Tidal Festival Playlist"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

(Run every command below from the project folder.)

## Usage

There are three ways to drive it — the same engine underneath.

### Web interface (recommended)

```bash
.venv/bin/python festival_cli.py -w 6660    # then open http://localhost:6660
.venv/bin/python festival_cli.py -w         # default port 666 (see note)
```

Opens the browser automatically: choose the line-up, set target / mode /
songs-per-band / name / dry-run, preview the bands, and hit **Build** with a
**live progress log** (the TIDAL login link shows up clickable there). Runs
only on `127.0.0.1`, uses nothing but the Python standard library.

> **Port 666 note:** ports below 1024 are privileged. The default `-w` (port
> 666) needs `sudo` on macOS/Linux (`sudo .venv/bin/python festival_cli.py -w`).
> Without root, just pick a higher port, e.g. `-w 6660`.

### Terminal menu

```bash
.venv/bin/python festival_cli.py
```

Menu-driven: pick the line-up, toggle target/mode/options, preview, build.
Settings are remembered in `festival_cli_config.json`.

### Command-line flags (for scripts / automation)

```bash
.venv/bin/python festival_playlist.py --target tidal --lineup "my_festival.txt"
.venv/bin/python festival_playlist.py --target tidal --lineup "my_festival.txt" --catalog --top 5
.venv/bin/python festival_playlist.py --target plex  --lineup "my_festival.txt" --name "My Festival 2026"
.venv/bin/python festival_playlist.py --target tidal --lineup "my_festival.txt" --dry-run
```

| Flag | Meaning |
|---|---|
| `--target tidal\|plex` | create a TIDAL playlist, or match against Plex |
| `--lineup FILE` | path to the line-up text file (required) |
| `--top N` | songs per band (default 10) |
| `--catalog` | most-played songs across *all* albums instead of the TIDAL top ranking |
| `--name NAME` | playlist name |
| `--dry-run` | only collect + write the to-do list, don't create a playlist |

## Line-up format

See [`example_lineup.txt`](example_lineup.txt):

```text
### Genres
Punk, Rock, Alternative        # comma-separated, highest priority first

### Line-Up
Dropkick Murphys               # one band per line
Bad Religion
```

- Blank lines and comment lines are ignored — a comment is a `#` followed by a
  space, or a lone `#`. (A band literally called `#1 Hit` is therefore kept.)
- Duplicate bands are removed automatically.
- The **genre list only matters for ambiguous band names** — see below.

## How bands are matched

- A search with a single clear hit is taken directly.
- With several candidates, an exact name match wins.
- Otherwise the genre priority list *would* decide — but note that the TIDAL API
  (`tidalapi` 0.8.x) does not expose genre data on artists/albums, so in practice
  the tool falls back to TIDAL's top hit and flags the band as *"please check"*.
- Anything it can't resolve or match is written to `manual_tasks.txt` —
  nothing is added blindly.

## TIDAL login & privacy

On the first run the tool prints (and, in the web UI, shows as a clickable link)
a TIDAL login URL. Authorise it once; the session is cached in
`tidal_session.json` (owner-readable `0600`, since it holds access tokens). That
file — and your saved settings — are **git-ignored** and must never be shared or
committed.

## Plex

Set your Plex credentials either in the web UI's Plex panel or via environment
variables `PLEX_BASEURL`, `PLEX_TOKEN`, `PLEX_LIBRARY`. Matching prefers an exact
title hit and falls back to compilation/various-artist entries; unmatched tracks
go to the to-do list.

## Tests

```bash
.venv/bin/pytest -q
```

101 tests, fully offline — TIDAL and Plex are mocked, the web server is exercised
on an ephemeral port. No network or login required.

## Files

- `festival_playlist.py` — core engine (line-up parser, TIDAL/Plex collection & playlist building) + `--flags` entry point
- `festival_cli.py` — interactive terminal menu; `-w` launches the web UI
- `festival_web.py` — local web interface (stdlib `http.server`, single-page UI, Server-Sent-Events live log)
- `example_lineup.txt` — impersonal sample line-up (the only tracked `.txt`)
- `requirements.txt` — `tidalapi` + `plexapi`
- `test_*.py` — offline test suite
- `images/` — logo
- `to-do.md`, `bug-fix.md` — open items and a documented, fixed upload bug

## Known limitations

- TIDAL's genre-based disambiguation is inert on `tidalapi` 0.8.x (no genre data);
  ambiguous names fall back to the top hit and are flagged for review.
- A few nice-to-haves (line-up file upload in the web UI, a cancel button) are
  tracked in [`to-do.md`](to-do.md).

## License

Personal hobby project, provided as-is. No formal license file — please ask
before redistributing.

## On LLM use

Built human-in-the-loop with an LLM: the idea and every design decision are
human, the implementation is largely model-written from targeted, well-scoped
prompts, reviewed and verified before landing.

---

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/lordvonbaum)

> Donations are voluntary and solely support the project. They do not influence
> the prioritisation of bugs, feature requests or support enquiries.
