"""
Tests fuer festival_playlist.py — laufen komplett offline (Tidal/Plex gemockt).

Ausfuehren:  .venv/bin/pytest -v
"""

import json
import os
from datetime import datetime
from unittest import mock

import pytest
import tidalapi

import festival_playlist as fp


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """time.sleep ueberall abschalten, damit die Tests schnell laufen."""
    monkeypatch.setattr(fp.time, "sleep", lambda *_: None)


_artist_id = iter(range(1, 10**6))

def make_artist(name, genre=None, albums=None, top_tracks=None):
    """Mock-Artist, der isinstance(x, tidalapi.artist.Artist) besteht."""
    a = mock.Mock(spec=tidalapi.artist.Artist)
    a.id = next(_artist_id)
    a.name = name
    a.genre = genre
    a.genres = None
    a.get_albums = mock.Mock(return_value=albums or [])
    a.get_top_tracks = mock.Mock(return_value=top_tracks or [])
    return a


def make_track(name, popularity=0, track_id=None):
    t = mock.Mock()
    t.name = name
    t.popularity = popularity
    t.id = track_id if track_id is not None else abs(hash(name)) % 10**6
    return t


def make_album(genre=None, tracks=None):
    alb = mock.Mock()
    alb.genre = genre
    alb.tracks = mock.Mock(return_value=tracks or [])
    return alb


def make_session(search_result):
    s = mock.Mock()
    s.search = mock.Mock(return_value=search_result)
    return s


# ---------------------------------------------------------------------------
# parse_lineup
# ---------------------------------------------------------------------------

class TestParseLineup:
    def test_reale_beispieldatei(self):
        genres, bands = fp.parse_lineup(
            os.path.join(os.path.dirname(__file__), "example_lineup.txt"))
        assert genres == ["punk", "rock", "alternative", "hardcore", "ska", "hip-hop"]
        assert len(bands) == 10
        assert bands[0] == "Dropkick Murphys"
        assert bands[-1] == "Millencolin"

    def test_basisformat(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_text("### Genres\nPunk, Rock\n### Line-Up\nBand A\nBand B\n",
                     encoding="utf-8")
        genres, bands = fp.parse_lineup(str(p))
        assert genres == ["punk", "rock"]
        assert bands == ["Band A", "Band B"]

    def test_kommentare_und_leerzeilen(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_text(
            "# Kopfkommentar\n\n### Genres\n# Kommentar\nPunk\n\n"
            "### Line-Up\nBand A\n# kein Bandname\n\nBand B\n",
            encoding="utf-8")
        genres, bands = fp.parse_lineup(str(p))
        assert genres == ["punk"]
        assert bands == ["Band A", "Band B"]

    def test_genres_dedupliziert_und_lowercase(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_text("### Genres\nPunk, punk, ROCK\n### Line-Up\nX\n",
                     encoding="utf-8")
        genres, _ = fp.parse_lineup(str(p))
        assert genres == ["punk", "rock"]

    def test_bands_header_variante(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_text("### Bands\nBand A\n", encoding="utf-8")
        genres, bands = fp.parse_lineup(str(p))
        assert genres == []
        assert bands == ["Band A"]

    def test_zeilen_vor_erster_sektion_ignoriert(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_text("Streuner\n### Line-Up\nBand A\n", encoding="utf-8")
        _, bands = fp.parse_lineup(str(p))
        assert bands == ["Band A"]

    def test_datei_fehlt(self):
        with pytest.raises(SystemExit):
            fp.parse_lineup("/nicht/vorhanden.txt")

    def test_keine_bands(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_text("### Genres\nPunk\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            fp.parse_lineup(str(p))

    def test_bom_am_dateianfang(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_bytes("### Genres\nPunk\n### Line-Up\nBand A\n".encode("utf-8-sig"))
        genres, bands = fp.parse_lineup(str(p))
        assert genres == ["punk"]  # Header trotz BOM erkannt
        assert bands == ["Band A"]

    def test_bandname_mit_hash_bleibt_erhalten(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_text("### Line-Up\n#1 Hit\n# echter Kommentar\n#\nBand B\n",
                     encoding="utf-8")
        _, bands = fp.parse_lineup(str(p))
        assert bands == ["#1 Hit", "Band B"]

    def test_doppelte_bands_werden_entfernt(self, tmp_path):
        p = tmp_path / "lineup.txt"
        p.write_text("### Line-Up\nBand A\nband a\nBand B\nBand A\n",
                     encoding="utf-8")
        _, bands = fp.parse_lineup(str(p))
        assert bands == ["Band A", "Band B"]

    def test_verbose_false_ist_still(self, tmp_path, capsys):
        p = tmp_path / "lineup.txt"
        p.write_text("### Line-Up\nBand A\n", encoding="utf-8")
        fp.parse_lineup(str(p), verbose=False)
        assert capsys.readouterr().out == ""

    def test_unbekannter_header_stoert_erfassung_nicht(self, tmp_path, capsys):
        p = tmp_path / "lineup.txt"
        p.write_text("### Genres\nPunk\n### Anreise\nZelt mitbringen\nP3\n"
                     "### Line-Up\nBand A\nBand B\n", encoding="utf-8")
        genres, bands = fp.parse_lineup(str(p))
        assert genres == ["punk"]
        assert bands == ["Band A", "Band B"]
        out = capsys.readouterr().out
        assert "unknown header '### Anreise'" in out

    def test_verzeichnis_statt_datei_meldet_sauber(self, tmp_path):
        with pytest.raises(SystemExit):
            fp.parse_lineup(str(tmp_path))  # Verzeichnis -> OSError -> sauberer Exit


# ---------------------------------------------------------------------------
# TaskLog
# ---------------------------------------------------------------------------

class TestTaskLog:
    def test_leer(self, tmp_path):
        t = fp.TaskLog()
        assert not t.has_tasks()
        out = tmp_path / "tasks.txt"
        t.write(str(out))
        assert "no open tasks" in out.read_text(encoding="utf-8")

    def test_mit_aufgaben(self, tmp_path):
        t = fp.TaskLog()
        t.not_found_artist.append("Unbekannte Band")
        t.genre_reject.append("Mehrdeutige Band")
        t.uncertain.append("Fallback Band -> Top Hit")
        t.no_tracks.append("Trackless")
        t.errors.append("Fehlerband (Suche: 429)")
        t.not_matched.append("Band - Song")
        assert t.has_tasks()
        assert t.count() == 6
        out = tmp_path / "tasks.txt"
        t.write(str(out))
        text = out.read_text(encoding="utf-8")
        for needle in ("Unbekannte Band", "Mehrdeutige Band",
                       "Fallback Band -> Top Hit", "Trackless",
                       "Fehlerband (Suche: 429)", "Band - Song"):
            assert needle in text


# ---------------------------------------------------------------------------
# Genre-Ermittlung
# ---------------------------------------------------------------------------

class TestGenrePriority:
    def test_artist_genre_attribute_gesammelt(self):
        artist = make_artist("X", genre="Punk")
        artist.genres = ["Rock", "Ska"]
        tags = fp.artist_genre_tags(artist)
        assert tags == {"punk", "rock", "ska"}

    def test_keine_album_api_calls(self):
        # tidalapi 0.8.x liefert keine Album-Genres -> get_albums()
        # darf hier nicht mehr aufgerufen werden (kostet nur API-Calls)
        artist = make_artist("X", genre="Punk")
        fp.artist_genre_tags(artist)
        artist.get_albums.assert_not_called()

    def test_prioritaet_bester_index(self):
        artist = make_artist("X", genre="Rock")
        artist.genres = ["Punk"]
        idx, tags = fp.genre_priority(artist, ["punk", "rock"])
        assert idx == 0  # punk schlaegt rock

    def test_kein_match(self):
        artist = make_artist("X", genre="Jazz")
        idx, _ = fp.genre_priority(artist, ["punk"])
        assert idx is None

    def test_substring_match(self):
        # 'punk' matcht auch 'punk rock' als Tag
        artist = make_artist("X", genre="Punk Rock")
        idx, _ = fp.genre_priority(artist, ["punk"])
        assert idx == 0


# ---------------------------------------------------------------------------
# find_artist
# ---------------------------------------------------------------------------

class TestFindArtist:
    def test_nicht_gefunden(self):
        s = make_session({"artists": []})
        artist, reason = fp.find_artist(s, "Niemand", ["punk"])
        assert artist is None and reason == "not_found"

    def test_einziger_treffer_direkt(self):
        a = make_artist("Solo Act", genre="Jazz")  # Genre egal bei Eindeutigkeit
        s = make_session({"artists": [a]})
        artist, reason = fp.find_artist(s, "Solo Act", ["punk"])
        assert artist is a and reason == "ok"

    def test_exakter_name_gewinnt(self):
        exact = make_artist("The Band")
        other = make_artist("The Band Experience")
        s = make_session({"artists": [other, exact]})
        artist, reason = fp.find_artist(s, "the band", ["punk"])
        assert artist is exact and reason == "ok"

    def test_mehrdeutig_genre_entscheidet(self):
        punker = make_artist("Focus", genre="Punk")
        jazzer = make_artist("Focus!", genre="Jazz")
        s = make_session({"artists": [jazzer, punker]})
        artist, reason = fp.find_artist(s, "Fokus", ["punk"])
        assert artist is punker and reason == "ok"

    def test_mehrdeutig_kein_genre_match(self):
        a = make_artist("A", genre="Jazz")
        b = make_artist("B", genre="Klassik")
        s = make_session({"artists": [a, b]})
        artist, reason = fp.find_artist(s, "AB", ["punk"])
        assert artist is None and reason == "genre_reject"

    def test_mehrdeutig_ohne_genreliste_nimmt_top_hit(self):
        a = make_artist("A")
        top = make_artist("Top")
        s = make_session({"artists": [a, top], "top_hit": top})
        artist, reason = fp.find_artist(s, "AB", [])
        assert artist is top and reason == "fallback"

    def test_top_hit_ausserhalb_der_exakten_kandidaten_verliert(self):
        exakt1, exakt2 = make_artist("Focus"), make_artist("Focus")
        fremd = make_artist("Focus Group")
        s = make_session({"artists": [exakt1, exakt2, fremd], "top_hit": fremd})
        artist, reason = fp.find_artist(s, "Focus", [])
        assert artist is exakt1 and reason == "fallback"

    def test_mehrdeutig_ohne_genre_daten_faellt_auf_top_hit(self):
        # tidalapi 0.8.x liefert keine Genres: statt alles abzulehnen,
        # Top-Hit nehmen und als 'fallback' (bitte pruefen) markieren
        a = make_artist("A", genre=None)
        top = make_artist("Top", genre=None)
        s = make_session({"artists": [a, top], "top_hit": top})
        artist, reason = fp.find_artist(s, "AB", ["punk"])
        assert artist is top and reason == "fallback"

    def test_fallback_ohne_top_hit_nimmt_ersten(self):
        a = make_artist("A", genre=None)
        b = make_artist("B", genre=None)
        s = make_session({"artists": [a, b]})
        artist, reason = fp.find_artist(s, "AB", ["punk"])
        assert artist is a and reason == "fallback"


# ---------------------------------------------------------------------------
# Sammel-Modi
# ---------------------------------------------------------------------------

class TestTracksCatalog:
    def test_dedup_hoechste_popularity_gewinnt(self):
        t_low = make_track("Song", popularity=10)
        t_high = make_track("Song", popularity=90)
        t_other = make_track("Anderer", popularity=50)
        artist = make_artist("X", albums=[
            make_album(tracks=[t_low, t_other]),
            make_album(tracks=[t_high]),
        ])
        result = fp.tracks_catalog(artist, 10)
        assert result == [t_high, t_other]

    def test_limit_und_ranking(self):
        tracks = [make_track(f"S{i}", popularity=i) for i in range(5)]
        artist = make_artist("X", albums=[make_album(tracks=tracks)])
        result = fp.tracks_catalog(artist, 3)
        assert [t.popularity for t in result] == [4, 3, 2]

    def test_album_fehler_wird_uebersprungen(self):
        kaputt = make_album()
        kaputt.tracks.side_effect = RuntimeError("API kaputt")
        ok = make_album(tracks=[make_track("Song", popularity=5)])
        artist = make_artist("X", albums=[kaputt, ok])
        result = fp.tracks_catalog(artist, 10)
        assert len(result) == 1 and result[0].name == "Song"


def make_plex_track(title, grandparent="", original=""):
    h = mock.Mock()
    h.title = title
    h.grandparentTitle = grandparent
    h.originalTitle = original
    return h


def make_tidal_track_for_plex(title, artist_name):
    t = mock.Mock()
    t.name = title
    t.artist = mock.Mock()
    t.artist.name = artist_name
    return t


class TestBuildPlexPlaylist:
    def _setup(self, monkeypatch, search_tracks):
        import plexapi.server
        music = mock.Mock()
        music.searchTracks = mock.Mock(side_effect=search_tracks)
        plex = mock.Mock()
        plex.library.section.return_value = music
        plex.createPlaylist = mock.Mock()
        monkeypatch.setattr(plexapi.server, "PlexServer",
                            mock.Mock(return_value=plex))
        return plex, music

    def test_direkter_treffer_und_exakt_bevorzugt(self, monkeypatch):
        # Gefilterte Suche liefert zwei Treffer; der exakte Titel gewinnt
        def search(title=None, filters=None):
            if filters:
                return [make_plex_track("Song (Live)"), make_plex_track("Song")]
            return []
        plex, _ = self._setup(monkeypatch, search)
        tasks = fp.TaskLog()
        collected = [("Band", make_tidal_track_for_plex("Song", "Band"))]
        fp.build_plex_playlist(collected, "PL", tasks)
        items = plex.createPlaylist.call_args.kwargs["items"]
        assert [h.title for h in items] == ["Song"]  # exakter Treffer
        assert tasks.not_matched == []

    def test_compilation_fallback_ueber_grandparent(self, monkeypatch):
        # Gefilterte Suche leer -> breite Suche + Album-/Track-Artist-Match
        def search(title=None, filters=None):
            if filters:
                return []
            return [make_plex_track("Song", grandparent="Various Artists",
                                    original="Die Band")]
        plex, _ = self._setup(monkeypatch, search)
        tasks = fp.TaskLog()
        collected = [("Die Band", make_tidal_track_for_plex("Song", "Die Band"))]
        fp.build_plex_playlist(collected, "PL", tasks)
        assert plex.createPlaylist.call_args.kwargs["items"]
        assert tasks.not_matched == []

    def test_kein_treffer_landet_in_aufgaben(self, monkeypatch):
        plex, _ = self._setup(monkeypatch, lambda title=None, filters=None: [])
        tasks = fp.TaskLog()
        collected = [("Band", make_tidal_track_for_plex("Song", "Band"))]
        fp.build_plex_playlist(collected, "PL", tasks)
        plex.createPlaylist.assert_not_called()
        assert tasks.not_matched == ["Band - Song"]


class TestCollect:
    def _session_mit(self, artists_by_query):
        s = mock.Mock()
        s.search = mock.Mock(
            side_effect=lambda q, **kw: {"artists": artists_by_query.get(q, [])})
        return s

    def test_happy_path_und_fehlerfaelle(self, capsys):
        top = [make_track("Hit 1", 90), make_track("Hit 2", 80)]
        gute_band = make_artist("Gute Band", top_tracks=top)
        leere_band = make_artist("Leere Band", top_tracks=[])
        s = self._session_mit({
            "Gute Band": [gute_band],
            "Leere Band": [leere_band],
            "Fehlt": [],
        })
        tasks = fp.TaskLog()
        collected = fp.collect(s, ["Gute Band", "Leere Band", "Fehlt"],
                               ["punk"], limit=2, catalog=False,
                               tasks=tasks, pause=0)
        assert [(b, t.name) for b, t in collected] == [
            ("Gute Band", "Hit 1"), ("Gute Band", "Hit 2")]
        assert tasks.no_tracks == ["Leere Band"]
        assert tasks.not_found_artist == ["Fehlt"]

    def test_track_fehler_landet_in_fehlerliste(self):
        kaputte = make_artist("Kaputte Band")
        kaputte.get_top_tracks.side_effect = RuntimeError("boom")
        s = self._session_mit({"Kaputte Band": [kaputte]})
        tasks = fp.TaskLog()
        collected = fp.collect(s, ["Kaputte Band"], [], limit=2,
                               catalog=False, tasks=tasks, pause=0)
        assert collected == []
        assert tasks.errors == ["Kaputte Band (tracks: boom)"]

    def test_such_fehler_bricht_lauf_nicht_ab(self):
        s = mock.Mock()
        gute = make_artist("Gute Band",
                           top_tracks=[make_track("Hit", 90)])
        s.search = mock.Mock(side_effect=[
            RuntimeError("429 Too Many Requests"),
            {"artists": [gute]},
        ])
        tasks = fp.TaskLog()
        collected = fp.collect(s, ["Limitierte Band", "Gute Band"], [],
                               limit=2, catalog=False, tasks=tasks, pause=0)
        assert len(collected) == 1  # zweite Band trotzdem gesammelt
        assert tasks.errors == ["Limitierte Band (search: 429 Too Many Requests)"]

    def test_fallback_landet_in_uncertain(self):
        a = make_artist("A", genre=None, top_tracks=[make_track("Hit", 90)])
        b = make_artist("B", genre=None)
        s = self._session_mit({"Mehrdeutig": [a, b]})
        tasks = fp.TaskLog()
        collected = fp.collect(s, ["Mehrdeutig"], ["punk"], limit=2,
                               catalog=False, tasks=tasks, pause=0)
        assert len(collected) == 1  # Tracks werden trotzdem gesammelt
        assert tasks.uncertain == ["Mehrdeutig -> A"]

    def test_doppelte_track_ids_werden_uebersprungen(self):
        gemeinsam = make_track("Kollabo", 90, track_id=555)
        a = make_artist("Band A", top_tracks=[gemeinsam, make_track("A-Song", 80)])
        b = make_artist("Band B", top_tracks=[gemeinsam, make_track("B-Song", 70)])
        s = self._session_mit({"Band A": [a], "Band B": [b]})
        tasks = fp.TaskLog()
        collected = fp.collect(s, ["Band A", "Band B"], [], limit=2,
                               catalog=False, tasks=tasks, pause=0)
        namen = [t.name for _, t in collected]
        assert namen == ["Kollabo", "A-Song", "B-Song"]


# ---------------------------------------------------------------------------
# Tidal-Playlist (Batching)
# ---------------------------------------------------------------------------

def make_http_error(status):
    """requests-artige Exception mit .response.status_code."""
    resp = mock.Mock()
    resp.status_code = status
    err = Exception(f"{status} Client Error")
    err.response = resp
    return err


def make_new_playlist_session(playlist):
    """Session, bei der noch KEINE Playlist gleichen Namens existiert."""
    session = mock.Mock()
    session.user.playlists.return_value = []
    session.user.create_playlist.return_value = playlist
    return session


class TestBuildTidalPlaylist:
    def test_batching_50er_bloecke(self):
        playlist = mock.Mock()
        session = make_new_playlist_session(playlist)
        collected = [("Band", make_track(f"S{i}", track_id=i))
                     for i in range(120)]
        fp.build_tidal_playlist(session, collected, "Test", catalog=False)
        batches = [c.args[0] for c in playlist.add.call_args_list]
        assert [len(b) for b in batches] == [50, 50, 20]
        assert [i for b in batches for i in b] == list(range(120))

    def test_leere_sammlung_legt_keine_playlist_an(self):
        session = mock.Mock()
        fp.build_tidal_playlist(session, [], "Leer", catalog=True)
        session.user.create_playlist.assert_not_called()

    def test_doppelte_ids_werden_dedupliziert(self):
        playlist = mock.Mock()
        session = make_new_playlist_session(playlist)
        t = make_track("Song", track_id=7)
        fp.build_tidal_playlist(session, [("A", t), ("B", t)], "Dup", False)
        playlist.add.assert_called_once_with([7])

    def test_412_wird_mit_frischem_etag_wiederholt(self):
        # 1. add() wirft 412 (veralteter ETag), danach klappt es mit
        # frisch geladener Playlist -> alle Tracks kommen an
        playlist = mock.Mock()
        playlist.id = "PL1"
        playlist.add.side_effect = [make_http_error(412), None, None]
        fresh = mock.Mock()
        fresh.id = "PL1"
        fresh.add.return_value = None
        session = make_new_playlist_session(playlist)
        session.playlist.return_value = fresh  # frisches Objekt beim Retry

        collected = [("B", make_track(f"S{i}", track_id=i)) for i in range(80)]
        fp.build_tidal_playlist(session, collected, "Test", False)

        session.playlist.assert_called_once_with("PL1")
        # Block 1 (50) einmal auf altem Objekt (412), dann auf frischem;
        # Block 2 (30) auf frischem. Alle 80 IDs landen in der Playlist.
        angekommen = ([i for c in playlist.add.call_args_list[1:] for i in c.args[0]]
                      + [i for c in fresh.add.call_args_list for i in c.args[0]])
        assert sorted(set(angekommen)) == list(range(80))

    def test_429_rate_limit_wird_wiederholt(self):
        # tidalapi wirft TooManyRequests (ohne .response) -> Backoff-Retry,
        # KEIN Neuladen der Playlist (nur 412 laedt neu)
        class TooManyRequests(Exception):
            pass
        playlist = mock.Mock()
        playlist.id = "PL1"
        playlist.add.side_effect = [TooManyRequests("429"), None]
        session = make_new_playlist_session(playlist)
        collected = [("B", make_track(f"S{i}", track_id=i)) for i in range(40)]
        fp.build_tidal_playlist(session, collected, "Test", False)
        session.playlist.assert_not_called()  # 429 laedt nicht neu
        angekommen = [i for c in playlist.add.call_args_list for i in c.args[0]]
        assert sorted(set(angekommen)) == list(range(40))

    def test_nicht_behebbarer_fehler_wird_nicht_wiederholt(self):
        playlist = mock.Mock()
        playlist.id = "PL1"
        playlist.add.side_effect = ValueError("kaputt")
        session = make_new_playlist_session(playlist)
        collected = [("B", make_track("S", track_id=1))]
        fp.build_tidal_playlist(session, collected, "Test", False)
        assert playlist.add.call_count == 1  # kein Retry

    def test_412_dauerhaft_bricht_sauber_ab(self):
        playlist = mock.Mock()
        playlist.id = "PL1"
        playlist.add.side_effect = make_http_error(412)
        session = make_new_playlist_session(playlist)
        session.playlist.return_value = playlist
        collected = [("B", make_track(f"S{i}", track_id=i)) for i in range(50)]
        # darf nicht durchschlagen, sondern sauber abbrechen
        fp.build_tidal_playlist(session, collected, "Test", False)

    def test_bestehende_playlist_wird_ergaenzt_ohne_duplikate(self):
        # Playlist gleichen Namens existiert schon mit Tracks 0,1,2
        vorhandene = mock.Mock()
        vorhandene.name = "Rodeo"
        vorhandene.id = "PLX"
        vorhandene.tracks.side_effect = [
            [make_track(f"S{i}", track_id=i) for i in range(3)],  # Seite 1
            [],
        ]
        vorhandene.add.return_value = None
        session = mock.Mock()
        session.user.playlists.return_value = [vorhandene]

        collected = [("B", make_track(f"S{i}", track_id=i)) for i in range(5)]
        fp.build_tidal_playlist(session, collected, "Rodeo", False)

        session.user.create_playlist.assert_not_called()  # nichts Neues angelegt
        added = [i for c in vorhandene.add.call_args_list for i in c.args[0]]
        assert added == [3, 4]  # nur die fehlenden Tracks

    def test_vollstaendige_playlist_fuegt_nichts_hinzu(self):
        vorhandene = mock.Mock()
        vorhandene.name = "Rodeo"
        vorhandene.id = "PLX"
        vorhandene.tracks.side_effect = [
            [make_track(f"S{i}", track_id=i) for i in range(3)], []]
        session = mock.Mock()
        session.user.playlists.return_value = [vorhandene]
        collected = [("B", make_track(f"S{i}", track_id=i)) for i in range(3)]
        fp.build_tidal_playlist(session, collected, "Rodeo", False)
        vorhandene.add.assert_not_called()


class TestPlaylistHelfer:
    def test_playlist_track_ids_paginiert(self):
        pl = mock.Mock()
        seite1 = [make_track(f"S{i}", track_id=i) for i in range(100)]
        seite2 = [make_track(f"S{i}", track_id=i) for i in range(100, 130)]
        pl.tracks.side_effect = [seite1, seite2]
        ids = fp._playlist_track_ids(pl)
        assert ids == set(range(130))
        assert pl.tracks.call_count == 2  # zwei Seiten, dann Stopp (<100)

    def test_existing_playlist_exakter_name(self):
        a, b = mock.Mock(), mock.Mock()
        a.name, b.name = "Anderes", "  Rodeo  "
        session = mock.Mock()
        session.user.playlists.return_value = [a, b]
        assert fp._existing_playlist(session, "Rodeo") is b

    def test_existing_playlist_keine_treffer(self):
        session = mock.Mock()
        session.user.playlists.return_value = []
        assert fp._existing_playlist(session, "Rodeo") is None


# ---------------------------------------------------------------------------
# Session-Cache
# ---------------------------------------------------------------------------

class TestTidalLogin:
    def test_kaputter_cache_faellt_auf_oauth_zurueck(self, tmp_path, monkeypatch):
        cache = tmp_path / "session.json"
        cache.write_text("das ist kein json", encoding="utf-8")
        monkeypatch.setattr(fp, "SESSION_FILE", str(cache))

        fake = mock.Mock()
        fake.check_login.return_value = True
        fake.token_type = "Bearer"
        fake.access_token = "at"
        fake.refresh_token = "rt"
        fake.expiry_time = "2099-01-01"
        monkeypatch.setattr(fp.tidalapi, "Session", mock.Mock(return_value=fake))

        session = fp.tidal_login()
        assert session is fake
        fake.login_oauth_simple.assert_called_once()
        # Cache wurde neu geschrieben und ist gueltiges JSON
        d = json.loads(cache.read_text(encoding="utf-8"))
        assert d["access_token"] == "at"

    def test_gueltiger_cache_ohne_neuen_login(self, tmp_path, monkeypatch):
        cache = tmp_path / "session.json"
        cache.write_text(json.dumps({
            "token_type": "Bearer", "access_token": "at",
            "refresh_token": "rt", "expiry_time": "2099-01-01T00:00:00",
        }), encoding="utf-8")
        monkeypatch.setattr(fp, "SESSION_FILE", str(cache))

        fake = mock.Mock()
        fake.load_oauth_session.return_value = True
        fake.check_login.return_value = True
        monkeypatch.setattr(fp.tidalapi, "Session", mock.Mock(return_value=fake))

        session = fp.tidal_login()
        assert session is fake
        fake.login_oauth_simple.assert_not_called()
        # expiry_time kommt als datetime an, nicht als String
        # (load_oauth_session erwartet Optional[datetime])
        exp = fake.load_oauth_session.call_args.args[3]
        assert isinstance(exp, datetime)

    def test_session_datei_nur_fuer_besitzer_lesbar(self, tmp_path, monkeypatch):
        cache = tmp_path / "session.json"
        monkeypatch.setattr(fp, "SESSION_FILE", str(cache))
        fake = mock.Mock()
        fake.check_login.return_value = True
        fake.token_type, fake.access_token = "Bearer", "at"
        fake.refresh_token, fake.expiry_time = "rt", None
        monkeypatch.setattr(fp.tidalapi, "Session", mock.Mock(return_value=fake))
        fp.tidal_login()
        assert (cache.stat().st_mode & 0o777) == 0o600

    def test_login_timeout_gibt_verstaendliche_meldung(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fp, "SESSION_FILE", str(tmp_path / "s.json"))
        fake = mock.Mock()
        fake.login_oauth_simple.side_effect = TimeoutError()
        monkeypatch.setattr(fp.tidalapi, "Session", mock.Mock(return_value=fake))
        with pytest.raises(SystemExit) as exc:
            fp.tidal_login()
        assert "login" in str(exc.value)


class TestExpiryHelfer:
    def test_iso_string_wird_datetime(self):
        assert fp._expiry_load("2099-01-01T12:30:00") == datetime(2099, 1, 1, 12, 30)

    def test_unsinn_wird_none(self):
        assert fp._expiry_load("kein datum") is None

    def test_none_bleibt_none(self):
        assert fp._expiry_load(None) is None
        assert fp._expiry_store(None) is None

    def test_datetime_wird_iso(self):
        assert fp._expiry_store(datetime(2099, 1, 1)) == "2099-01-01T00:00:00"


class TestTracksCatalogGetter:
    def test_bevorzugt_aktuelle_methode(self):
        artist = make_artist("X")
        artist.get_ep_singles = mock.Mock(return_value=[])
        artist.get_albums_ep_singles = mock.Mock(return_value=[])
        fp.tracks_catalog(artist, 5)
        artist.get_ep_singles.assert_called_once()
        artist.get_albums_ep_singles.assert_not_called()


class TestMainPlexVorpruefung:
    def test_platzhalter_bricht_vor_sammelphase_ab(self, monkeypatch):
        monkeypatch.setattr(fp.sys, "argv", [
            "festival_playlist.py", "--target", "plex", "--lineup", "rodeo.txt"])
        login = mock.Mock()
        monkeypatch.setattr(fp, "tidal_login", login)
        with pytest.raises(SystemExit) as exc:
            fp.main()
        assert "placeholders" in str(exc.value)
        login.assert_not_called()  # Abbruch VOR Login und Sammelphase
