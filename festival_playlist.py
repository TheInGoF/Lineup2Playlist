#!/usr/bin/env python3
"""
Lineup2Playlist - core module (TIDAL source)
============================================

Generic tool: the line-up and genre priorities come from a text file.
Works for any festival.

Text format (see example_lineup.txt):
    ### Genres
    Punk, Rock, Alternative, Hip-Hop
    ### Line-Up
    Bad Religion
    The Offspring
    ...
  - Genres: comma-separated, priority DESCENDING (first = highest priority).
  - Line-Up: one band per line. Duplicates are removed automatically.
  - Blank lines and comment lines ('#' + space, or a lone '#') are ignored,
    so band names like '#1 Hit' are preserved.

Collection modes:
  Default    -> Artist.get_top_tracks(limit)   (TIDAL ranking, recency-biased)
  --catalog  -> most-played songs across ALL albums (a deeper best-of)

Ambiguous band names:
  A search with a single clear hit is taken directly.
  With several candidates, an exact name match wins.
  After that the genre priority list would decide - but the TIDAL API
  (tidalapi 0.8.x) exposes no genre data on artists/albums. In that case the
  TIDAL top hit is used and the band is flagged for review in the task list.

Target backend:
  --target tidal  -> creates a TIDAL playlist directly
  --target plex   -> matches the tracks against your Plex library

Dependencies:
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
# CONFIGURATION
# ---------------------------------------------------------------------------

TOP_N = 10

# Anchored to the script directory so the session cache and task list are
# always the same files, independent of the current working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_FILE = os.path.join(BASE_DIR, "manual_tasks.txt")
SESSION_FILE = os.path.join(BASE_DIR, "tidal_session.json")

PLEX_BASEURL = "http://192.168.x.x:32400"
PLEX_TOKEN = "YOUR_PLEX_TOKEN"
PLEX_LIBRARY = "Music"


def _plex_is_placeholder():
    return "YOUR_PLEX_TOKEN" in PLEX_TOKEN or "192.168.x.x" in PLEX_BASEURL


# ---------------------------------------------------------------------------
# TEXT PARSER
# ---------------------------------------------------------------------------

def _is_comment(line):
    """A comment is '#' + space, or a lone '#'.
    This keeps band names like '#1 Hit'."""
    return line == "#" or line.startswith("# ")


def parse_lineup(path, verbose=True):
    """
    Read the line-up file and return (genres, bands).
    genres: lowercase list, in priority order (descending).
    bands:  list of band names (original spelling), deduplicated.
    """
    if not os.path.exists(path):
        sys.exit(f"Line-up file not found: {path}")

    genres, bands = [], []
    seen_bands = set()
    duplicates = 0
    strays = 0
    unknown = {}             # unknown '###' header -> ignored line count
    section = None
    unknown_header = None

    try:
        # utf-8-sig: a BOM at the start would otherwise hide the first header
        f = open(path, encoding="utf-8-sig", errors="replace")
    except OSError as e:
        sys.exit(f"Line-up file not readable: {path} ({e})")
    with f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            # detect section headers
            if line.startswith("###"):
                header = line.lstrip("#").strip().lower()
                unknown_header = None
                if header.startswith("genre"):
                    section = "genres"
                elif header.startswith("line") or header.startswith("band"):
                    section = "bands"
                else:
                    section = None
                    unknown_header = line
                continue

            if _is_comment(line):
                continue

            if section == "genres":
                for g in line.split(","):
                    g = g.strip().lower()
                    if g and g not in genres:
                        genres.append(g)
            elif section == "bands":
                key = line.lower()
                if key in seen_bands:
                    duplicates += 1
                    continue
                seen_bands.add(key)
                bands.append(line)
            elif unknown_header:
                unknown[unknown_header] = unknown.get(unknown_header, 0) + 1
            else:
                strays += 1

    if not bands:
        sys.exit("No bands found in the line-up. Is there anything under '### Line-Up'?")
    if verbose:
        if strays:
            print(f"  ! Ignored {strays} line(s) before the first '###' header.")
        for head, n in unknown.items():
            print(f"  ! Ignored {n} line(s) under unknown header '{head}'.")
        if duplicates:
            print(f"  ! Removed {duplicates} duplicate band line(s).")
        print(f"Line-up loaded: {len(bands)} bands, {len(genres)} genres "
              f"(priority: {', '.join(genres) if genres else '-'})\n")
    return genres, bands


# ---------------------------------------------------------------------------
# MANUAL TASK LIST
# ---------------------------------------------------------------------------

class TaskLog:
    def __init__(self):
        self.not_found_artist = []
        self.genre_reject = []
        self.uncertain = []
        self.no_tracks = []
        self.errors = []
        self.not_matched = []

    def _all(self):
        return (self.not_found_artist + self.genre_reject + self.uncertain
                + self.no_tracks + self.errors + self.not_matched)

    def count(self):
        return len(self._all())

    def has_tasks(self):
        return bool(self._all())

    def write(self, path):
        lines = ["# Lineup2Playlist - manual tasks",
                 "# NOT handled automatically by the script. Please check by hand.",
                 ""]
        if self.not_found_artist:
            lines.append("## Bands NOT found on TIDAL (check name/spelling):")
            lines += [f"  - {b}" for b in self.not_found_artist]
            lines.append("")
        if self.genre_reject:
            lines.append("## Ambiguous - no candidate with a matching genre:")
            lines += [f"  - {b}" for b in self.genre_reject]
            lines.append("")
        if self.uncertain:
            lines.append("## Ambiguous - auto-picked by top hit (please check):")
            lines += [f"  - {b}" for b in self.uncertain]
            lines.append("")
        if self.no_tracks:
            lines.append("## Bands without tracks (maybe not available on TIDAL):")
            lines += [f"  - {b}" for b in self.no_tracks]
            lines.append("")
        if self.errors:
            lines.append("## API errors during lookup (try again later):")
            lines += [f"  - {b}" for b in self.errors]
            lines.append("")
        if self.not_matched:
            lines.append("## Songs not found in the Plex library (add manually):")
            lines += [f"  - {s}" for s in self.not_matched]
            lines.append("")
        if not self.has_tasks():
            lines.append("All done automatically - no open tasks.")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\nTask list written: {path}")


# ---------------------------------------------------------------------------
# TIDAL LOGIN (with session cache)
# ---------------------------------------------------------------------------

def _expiry_load(value):
    """Turn a cached expiry_time string back into a datetime (or None)."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value


def _expiry_store(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value is not None else None


def _cache_session(session, quiet=False):
    try:
        # 0600: the file holds access/refresh tokens in clear text
        fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expiry_time": _expiry_store(session.expiry_time),
            }, f)
        os.chmod(SESSION_FILE, 0o600)
        if not quiet:
            print(f"TIDAL session cached: {SESSION_FILE}")
    except Exception as e:
        print(f"  ! Could not cache session: {e}", file=sys.stderr)


def tidal_login():
    session = tidalapi.Session()
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE) as f:
                d = json.load(f)
            ok = session.load_oauth_session(
                d["token_type"], d["access_token"],
                d.get("refresh_token"), _expiry_load(d.get("expiry_time")),
            )
            if ok and session.check_login():
                print("TIDAL session loaded from cache.")
                # tidalapi refreshes expired tokens automatically on load
                # -> write the renewed token set back, otherwise every further
                # run repeats the same refresh round-trip
                if session.access_token != d.get("access_token"):
                    _cache_session(session, quiet=True)
                return session
        except Exception:
            pass

    try:
        session.login_oauth_simple()
    except TimeoutError:
        sys.exit("TIDAL login timed out: the link was not confirmed in time. "
                 "Please start again.")
    if not session.check_login():
        sys.exit("TIDAL login failed.")

    _cache_session(session)
    return session


# ---------------------------------------------------------------------------
# GENRE DETECTION
# ---------------------------------------------------------------------------

def artist_genre_tags(artist):
    """
    Collect genre hints from the artist object (lowercase set).

    Note: tidalapi 0.8.x sets NO genre attributes on artists/albums, so this
    set is always empty there. The attribute check is kept for future library
    versions (it costs no API calls).
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
    Return (index, tags): index (0 = highest priority) of the best matching
    genre, or None if no listed genre matches; tags is the set of genre hints
    found.
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
# FIND ARTIST (genre only when ambiguous)
# ---------------------------------------------------------------------------

# From how many serious hits a search counts as "ambiguous".
AMBIGUOUS_THRESHOLD = 2

def find_artist(session, name, priority_list):
    """
    Return: (artist|None, reason) with reason in
    {"ok", "fallback", "not_found", "genre_reject"}.
    - Single hit / unique exact name match -> taken directly ("ok").
    - Several candidates -> genre priority decides, if the API provides genre
      data. If it does not (tidalapi 0.8.x) or there is no priority list, the
      TIDAL top hit (if among the candidates) or the first candidate is taken
      and marked "fallback" -> flagged for review in the task list.
    """
    res = session.search(name, models=[tidalapi.artist.Artist], limit=5)
    artists = res.get("artists") or []
    if not artists:
        return None, "not_found"

    # Unambiguous: single hit -> take it directly
    if len(artists) < AMBIGUOUS_THRESHOLD:
        return artists[0], "ok"

    # An exact name match among the candidates? Then it isn't really ambiguous
    # - the exact hit wins (the most common normal case).
    exact = [a for a in artists if (a.name or "").strip().lower() == name.strip().lower()]
    if len(exact) == 1:
        return exact[0], "ok"

    candidates = list(exact) if exact else list(artists)

    # Apply genre priority, if the API provides genres at all
    if priority_list:
        tagged = [(cand, genre_priority(cand, priority_list)) for cand in candidates]
        if any(tags for _, (_, tags) in tagged):
            best_artist, best_idx = None, None
            for cand, (idx, _) in tagged:
                if idx is not None and (best_idx is None or idx < best_idx):
                    best_artist, best_idx = cand, idx
            if best_artist is not None:
                print(f"     [genre disambig] '{name}' -> {best_artist.name} "
                      f"(priority genre: {priority_list[best_idx]})")
                return best_artist, "ok"
            return None, "genre_reject"

    # No genre data available or no priority list
    # -> TIDAL top hit, but only if it's among the candidates
    # (candidates may already be narrowed to exact name matches)
    top = res.get("top_hit")
    chosen = candidates[0]
    if isinstance(top, tidalapi.artist.Artist):
        for cand in candidates:
            if getattr(cand, "id", None) == getattr(top, "id", None):
                chosen = cand
                break
    print(f"     [top-hit fallback] '{name}' -> {chosen.name} (ambiguous, "
          f"please check)")
    return chosen, "fallback"


# ---------------------------------------------------------------------------
# COLLECTION MODES
# ---------------------------------------------------------------------------

def tracks_top(artist, limit):
    return artist.get_top_tracks(limit)


def tracks_catalog(artist, limit):
    try:
        albums = artist.get_albums()
    except Exception:
        albums = []
    # get_ep_singles is the current method, get_albums_ep_singles the
    # deprecated fallback for older tidalapi versions.
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
    mode = "Catalog" if catalog else "Top tracks"
    print(f"Collection mode: {mode}, {limit} songs per band\n")

    for band in bands:
        try:
            artist, reason = find_artist(session, band, priority_list)
        except Exception as e:
            print(f"-> {band}: API error during search ({e})")
            tasks.errors.append(f"{band} (search: {e})")
            time.sleep(pause)
            continue

        if artist is None:
            if reason == "genre_reject":
                print(f"-> {band}: ambiguous, no genre match -> manual task")
                tasks.genre_reject.append(band)
            else:
                print(f"-> {band}: NOT found")
                tasks.not_found_artist.append(band)
            time.sleep(pause)
            continue

        if reason == "fallback":
            tasks.uncertain.append(f"{band} -> {artist.name}")

        try:
            tracks = tracks_catalog(artist, limit) if catalog else tracks_top(artist, limit)
        except Exception as e:
            print(f"-> {band}: error loading tracks ({e})")
            tasks.errors.append(f"{band} (tracks: {e})")
            time.sleep(pause)
            continue

        if not tracks:
            print(f"-> {band}: no tracks")
            tasks.no_tracks.append(band)
            time.sleep(pause)
            continue

        print(f"-> {artist.name}")
        for t in tracks:
            tid = getattr(t, "id", None)
            if tid is not None and tid in seen_track_ids:
                print(f"     {t.name}  (duplicate, skipped)")
                continue
            if tid is not None:
                seen_track_ids.add(tid)
            pop = getattr(t, "popularity", None)
            suffix = f"  (pop {pop})" if pop is not None else ""
            print(f"     {t.name}{suffix}")
            collected.append((band, t))
        time.sleep(pause)

    print(f"\nTotal: {len(collected)} tracks collected.\n")
    return collected


# ---------------------------------------------------------------------------
# TARGETS
# ---------------------------------------------------------------------------

def _http_status(exc):
    """Pull the HTTP status code from a requests exception (or None)."""
    return getattr(getattr(exc, "response", None), "status_code", None)


def _is_rate_limit(exc):
    """Detect 429 - including tidalapi's TooManyRequests (has no .response)."""
    return _http_status(exc) == 429 or type(exc).__name__ == "TooManyRequests"


def _existing_playlist(session, name):
    """Find an existing UserPlaylist with exactly this name (or None)."""
    try:
        for p in session.user.playlists():
            if (getattr(p, "name", "") or "").strip() == name.strip():
                return p
    except Exception:
        pass
    return None


def _playlist_track_ids(playlist):
    """Read all track IDs of a playlist, paginated (TIDAL caps at ~100)."""
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
    Add one block, with retries for two known transient errors:
      - 412 (stale ETag): reload the playlist object, then retry.
      - 429 / TooManyRequests (rate limit): retry with backoff.
    Other errors are re-raised immediately. Returns (playlist, success);
    playlist may have been replaced by a freshly loaded object.
    """
    last_exc = None
    for attempt in range(retries):
        try:
            playlist.add(ids)
            return playlist, True
        except Exception as e:
            last_exc = e
            if not (_http_status(e) == 412 or _is_rate_limit(e)):
                raise  # not recoverable -> bubble up
            if attempt < retries - 1:
                if _http_status(e) == 412:
                    try:
                        playlist = session.playlist(playlist.id)  # fresh ETag
                    except Exception:
                        try:
                            playlist._reparse()
                        except Exception:
                            pass
                time.sleep(pause * (attempt + 1))  # backoff (esp. for 429)
    if last_exc is not None:
        print(f"  ! Gave up on block after {retries} attempts: {last_exc}",
              file=sys.stderr)
    return playlist, False


def build_tidal_playlist(session, collected, name, catalog):
    if not collected:
        print("No tracks collected - no TIDAL playlist created.")
        return
    desc = ("Most-played songs across all albums" if catalog
            else "Most popular songs (TIDAL ranking)")

    # Deduplicated in collection order (TIDAL silently skips dupes)
    track_ids = list(dict.fromkeys(t.id for _, t in collected))

    # Reuse an existing playlist of the same name -> resume after an abort,
    # WITHOUT adding already-present tracks a second time.
    playlist = _existing_playlist(session, name)
    if playlist is not None:
        present = _playlist_track_ids(playlist)
        missing = [tid for tid in track_ids if tid not in present]
        print(f"Found existing playlist '{name}' ({len(present)} tracks)"
              f" - adding {len(missing)} missing.")
    else:
        playlist = session.user.create_playlist(name, desc)
        present = set()
        missing = track_ids

    if not missing:
        print(f"Playlist '{name}' is already complete ({len(present)} tracks).")
        return

    BATCH = 50
    added = 0
    for i in range(0, len(missing), BATCH):
        block = missing[i:i + BATCH]
        try:
            playlist, ok = _add_batch_with_retry(session, playlist, block)
        except Exception as e:
            print(f"  ! Error adding from track {i + 1}: {e}", file=sys.stderr)
            ok = False
        if not ok:
            print(f"  ! Block from track {i + 1} not added. Another run will "
                  f"add the remaining tracks (without duplicates).")
            break
        added += len(block)
        time.sleep(0.3)  # short pause -> avoid provoking an ETag conflict

    print(f"TIDAL playlist '{name}': {added} newly added, "
          f"{len(present) + added} tracks in total.")


def build_plex_playlist(collected, name, tasks):
    from plexapi.server import PlexServer

    plex = PlexServer(PLEX_BASEURL, PLEX_TOKEN)
    music = plex.library.section(PLEX_LIBRARY)

    def _best_hit(hits, title):
        """Prefer an exact title match (searchTracks matches substrings)."""
        exact = [h for h in hits
                 if (h.title or "").strip().lower() == title.strip().lower()]
        return exact[0] if exact else hits[0]

    matched = []
    for band, t in collected:
        artist_name = t.artist.name if getattr(t, "artist", None) else band
        title = t.name
        hits = music.searchTracks(title=title, filters={"artist.title": artist_name})
        if not hits:
            # Fallback: also check the track artist (originalTitle) so
            # compilation tracks are found (there the album artist is
            # e.g. 'Various Artists').
            wanted = artist_name.lower()
            hits = [h for h in music.searchTracks(title=title)
                    if wanted in (h.grandparentTitle or "").lower()
                    or wanted in (getattr(h, "originalTitle", "") or "").lower()]
        if hits:
            matched.append(_best_hit(hits, title))
        else:
            tasks.not_matched.append(f"{artist_name} - {title}")

    if matched:
        plex.createPlaylist(name, items=matched)
        print(f"Plex playlist '{name}' created: {len(matched)} tracks.")
    else:
        print("No tracks found in the Plex library - no playlist created.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Lineup2Playlist (TIDAL/Plex)")
    ap.add_argument("--target", choices=["tidal", "plex"], required=True)
    ap.add_argument("--lineup", required=True,
                    help="path to the line-up .txt (required, e.g. example_lineup.txt)")
    ap.add_argument("--top", type=int, default=TOP_N,
                    help=f"songs per band (default {TOP_N})")
    ap.add_argument("--catalog", action="store_true",
                    help="most-played songs across ALL albums instead of the top ranking")
    ap.add_argument("--name", default="Festival - Best Of")
    ap.add_argument("--dry-run", action="store_true",
                    help="only collect + write the task list, don't create a playlist")
    args = ap.parse_args()

    # Plex pre-check BEFORE the (long) collection phase, not after
    if args.target == "plex" and not args.dry_run:
        try:
            import plexapi  # noqa: F401
        except ImportError:
            sys.exit("The 'plexapi' package is not installed: pip install plexapi")
        if _plex_is_placeholder():
            sys.exit("PLEX_BASEURL/PLEX_TOKEN are still placeholders - set them at "
                     "the top of the script (or use the interactive CLI "
                     "festival_cli.py).")

    priority_list, bands = parse_lineup(args.lineup)

    tasks = TaskLog()
    session = tidal_login()

    collected = collect(session, bands, priority_list, args.top, args.catalog, tasks)

    build_error = None
    if args.dry_run:
        print("Dry run: no playlist created.")
    else:
        try:
            if args.target == "tidal":
                build_tidal_playlist(session, collected, args.name, args.catalog)
            else:
                build_plex_playlist(collected, args.name, tasks)
        except Exception as e:
            build_error = e
            print(f"ERROR creating the playlist: {type(e).__name__}: {e}",
                  file=sys.stderr)

    # Always write the task list, even if building the playlist fails
    tasks.write(TASK_FILE)
    if tasks.has_tasks():
        print("There are open manual tasks - see the file above.")

    if build_error is not None:
        sys.exit(1)


if __name__ == "__main__":
    main()
