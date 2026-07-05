"""
Tests fuer festival_cli.py — laufen komplett offline, Eingaben gemockt.

Ausfuehren:  .venv/bin/pytest -v
"""

import json
import os
from unittest import mock

import pytest

import festival_cli as fc
import festival_playlist as fp


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """CONFIG_FILE in ein Temp-Verzeichnis umbiegen."""
    cfg_file = tmp_path / "cli_config.json"
    monkeypatch.setattr(fc, "CONFIG_FILE", str(cfg_file))
    return cfg_file


def feed_inputs(monkeypatch, answers):
    """input() liefert nacheinander die Antworten, danach EOF."""
    it = iter(answers)
    def fake_input(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    monkeypatch.setattr("builtins.input", fake_input)


# ---------------------------------------------------------------------------
# Line-Up-Erkennung
# ---------------------------------------------------------------------------

class TestIsLineupFile:
    def test_lineup_header(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("### Line-Up\nBand\n", encoding="utf-8")
        assert fc.is_lineup_file(str(p))

    def test_bands_header(self, tmp_path):
        p = tmp_path / "b.txt"
        p.write_text("### Bands\nBand\n", encoding="utf-8")
        assert fc.is_lineup_file(str(p))

    def test_normale_textdatei(self, tmp_path):
        p = tmp_path / "c.txt"
        p.write_text("nur Text\n# Kommentar\n", encoding="utf-8")
        assert not fc.is_lineup_file(str(p))

    def test_nicht_vorhanden(self, tmp_path):
        assert not fc.is_lineup_file(str(tmp_path / "fehlt.txt"))

    def test_reale_beispieldatei(self):
        real = os.path.join(os.path.dirname(fc.__file__), "example_lineup.txt")
        assert fc.is_lineup_file(real)


class TestFindLineupFiles:
    def test_findet_nur_lineups_und_ignoriert_taskfile(self, tmp_path, monkeypatch):
        (tmp_path / "lineup.txt").write_text("### Line-Up\nX\n", encoding="utf-8")
        (tmp_path / "notizen.txt").write_text("nur Text\n", encoding="utf-8")
        (tmp_path / os.path.basename(fp.TASK_FILE)).write_text(
            "### Line-Up\nX\n", encoding="utf-8")
        monkeypatch.setattr(fc, "__file__", str(tmp_path / "festival_cli.py"))
        hits = fc.find_lineup_files()
        assert [os.path.basename(h) for h in hits] == ["lineup.txt"]


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_ohne_datei(self, tmp_config):
        cfg = fc.load_config()
        assert cfg["target"] == "tidal"
        assert cfg["top"] == fp.TOP_N
        assert cfg["dry_run"] is False

    def test_kaputte_datei_faellt_auf_defaults(self, tmp_config):
        tmp_config.write_text("kein json", encoding="utf-8")
        assert fc.load_config()["target"] == "tidal"

    def test_speichern_und_laden(self, tmp_config, tmp_path):
        lineup = tmp_path / "l.txt"
        lineup.write_text("### Line-Up\nX\n", encoding="utf-8")
        cfg = fc.load_config()
        cfg.update(target="plex", top=5, lineup=str(lineup), dry_run=True)
        fc.save_config(cfg)
        neu = fc.load_config()
        assert neu["target"] == "plex"
        assert neu["top"] == 5
        assert neu["lineup"] == str(lineup)
        assert neu["dry_run"] is True

    def test_verschwundene_lineup_datei_wird_zurueckgesetzt(self, tmp_config):
        cfg = fc.load_config()
        cfg["lineup"] = "/gibt/es/nicht.txt"
        fc.save_config(cfg)
        assert fc.load_config()["lineup"] is None

    def test_unbekannte_keys_werden_ignoriert(self, tmp_config):
        tmp_config.write_text(json.dumps({"target": "plex", "boese": 1}),
                              encoding="utf-8")
        cfg = fc.load_config()
        assert cfg["target"] == "plex"
        assert "boese" not in cfg

    def test_env_vars_schlagen_gespeicherte_config(self, tmp_config, monkeypatch):
        tmp_config.write_text(json.dumps({"plex_token": "alt",
                                          "plex_baseurl": "http://alt:32400"}),
                              encoding="utf-8")
        monkeypatch.setenv("PLEX_TOKEN", "neu-aus-env")
        monkeypatch.setenv("PLEX_BASEURL", "http://neu:32400")
        cfg = fc.load_config()
        assert cfg["plex_token"] == "neu-aus-env"
        assert cfg["plex_baseurl"] == "http://neu:32400"

    def test_config_datei_nur_fuer_besitzer_lesbar(self, tmp_config):
        fc.save_config(fc.load_config())
        assert (tmp_config.stat().st_mode & 0o777) == 0o600

    def test_verzeichnis_als_lineup_wird_verworfen(self, tmp_config, tmp_path):
        cfg = fc.load_config()
        cfg["lineup"] = str(tmp_path)  # ein Verzeichnis, keine Datei
        fc.save_config(cfg)
        assert fc.load_config()["lineup"] is None


class TestSuggestName:
    def test_vorschlag_aus_dateiname(self):
        cfg = dict(fc.DEFAULTS)
        cfg["lineup"] = "/pfad/ruhrpott_rodeo.txt"
        fc._suggest_name(cfg)
        assert cfg["name"] == "Ruhrpott Rodeo - Best Of"

    def test_eigener_name_bleibt(self):
        cfg = dict(fc.DEFAULTS)
        cfg.update(lineup="/pfad/rodeo.txt", name="Meine Liste")
        fc._suggest_name(cfg)
        assert cfg["name"] == "Meine Liste"


class TestPlexReady:
    def test_platzhalter_nicht_bereit(self):
        cfg = dict(fc.DEFAULTS)
        cfg.update(plex_baseurl="http://192.168.x.x:32400",
                   plex_token="YOUR_PLEX_TOKEN")
        assert not fc.plex_ready(cfg)

    def test_echte_werte_bereit(self):
        cfg = dict(fc.DEFAULTS)
        cfg.update(plex_baseurl="http://10.0.0.5:32400", plex_token="abc123")
        assert fc.plex_ready(cfg)


# ---------------------------------------------------------------------------
# Eingabe-Helfer
# ---------------------------------------------------------------------------

class TestAsk:
    def test_default_bei_leerer_eingabe(self, monkeypatch):
        feed_inputs(monkeypatch, [""])
        assert fc.ask("Frage", "standard") == "standard"

    def test_eof_gibt_none(self, monkeypatch):
        feed_inputs(monkeypatch, [])
        assert fc.ask("Frage") is None

    def test_ask_int_wiederholt_bei_unsinn(self, monkeypatch):
        feed_inputs(monkeypatch, ["abc", "999", "7"])
        assert fc.ask_int("Zahl", 10, 1, 50) == 7


# ---------------------------------------------------------------------------
# Menue-Ablauf (End-to-End mit gemockten Eingaben)
# ---------------------------------------------------------------------------

class TestMenuFlow:
    def test_umschalten_und_beenden_speichert(self, tmp_config, monkeypatch):
        monkeypatch.setattr(fc, "find_lineup_files", lambda: [])
        monkeypatch.setattr(fc.sys, "argv", ["festival_cli.py"])
        # Ziel -> plex, Modus -> Katalog, Dry-Run an, beenden
        feed_inputs(monkeypatch, ["2", "3", "6", "q"])
        fc.main()
        saved = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert saved["target"] == "plex"
        assert saved["catalog"] is True
        assert saved["dry_run"] is True

    def test_start_ohne_lineup_bricht_sauber_ab(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr(fc, "find_lineup_files", lambda: [])
        monkeypatch.setattr(fc.sys, "argv", ["festival_cli.py"])
        feed_inputs(monkeypatch, ["s", "q"])
        fc.main()
        out = capsys.readouterr().out
        assert "line-up file first" in out

    def test_dry_run_lauf_ohne_playlist(self, tmp_config, tmp_path, monkeypatch, capsys):
        lineup = tmp_path / "mini.txt"
        lineup.write_text("### Line-Up\nBand A\n", encoding="utf-8")
        monkeypatch.setattr(fc, "find_lineup_files", lambda: [str(lineup)])
        monkeypatch.setattr(fc.sys, "argv", ["festival_cli.py"])
        monkeypatch.setattr(fc.fp.time, "sleep", lambda *_: None)

        artist = mock.Mock()
        artist.name = "Band A"
        track = mock.Mock()
        track.name = "Hit"
        track.popularity = 50
        artist.get_top_tracks.return_value = [track]
        session = mock.Mock()
        session.search.return_value = {"artists": [artist]}
        monkeypatch.setattr(fc.fp, "tidal_login", lambda: session)
        monkeypatch.setattr(fc.fp, "TASK_FILE", str(tmp_path / "tasks.txt"))
        build = mock.Mock()
        monkeypatch.setattr(fc.fp, "build_tidal_playlist", build)

        # Dry-Run an, Start, Bestaetigung, <Enter> nach dem Lauf, beenden
        feed_inputs(monkeypatch, ["6", "s", "y", "", "q"])
        fc.main()

        out = capsys.readouterr().out
        assert "Dry run: no playlist created" in out
        build.assert_not_called()
        assert (tmp_path / "tasks.txt").exists()


class TestRunGeneration:
    def _minimal_cfg(self, lineup, **over):
        cfg = dict(fc.DEFAULTS)
        cfg.update(lineup=str(lineup), name="PL", top=2)
        cfg.update(over)
        return cfg

    def test_dry_run_plex_wird_nicht_durch_platzhalter_blockiert(
            self, tmp_path, monkeypatch, capsys):
        lineup = tmp_path / "l.txt"
        lineup.write_text("### Line-Up\nBand A\n", encoding="utf-8")
        artist = mock.Mock(); artist.name = "Band A"
        track = mock.Mock(); track.name = "Hit"; track.popularity = 50
        artist.get_top_tracks.return_value = [track]
        session = mock.Mock(); session.search.return_value = {"artists": [artist]}
        monkeypatch.setattr(fc.fp, "tidal_login", lambda: session)
        monkeypatch.setattr(fc.fp, "TASK_FILE", str(tmp_path / "t.txt"))
        monkeypatch.setattr(fc.fp.time, "sleep", lambda *_: None)
        build_plex = mock.Mock()
        monkeypatch.setattr(fc.fp, "build_plex_playlist", build_plex)

        # Ziel plex + Platzhalter-Creds, aber Dry-Run -> darf NICHT blocken
        cfg = self._minimal_cfg(lineup, target="plex", dry_run=True)
        feed_inputs(monkeypatch, ["y", ""])  # Start bestaetigen, dann pause
        fc.run_generation(cfg)

        out = capsys.readouterr().out
        assert "Dry run: no playlist created" in out
        build_plex.assert_not_called()

    def test_task_liste_ueberlebt_build_fehler(self, tmp_path, monkeypatch):
        lineup = tmp_path / "l.txt"
        lineup.write_text("### Line-Up\nBand A\n", encoding="utf-8")
        artist = mock.Mock(); artist.name = "Band A"
        track = mock.Mock(); track.name = "Hit"; track.popularity = 50
        track.id = 1
        artist.get_top_tracks.return_value = [track]
        session = mock.Mock(); session.search.return_value = {"artists": [artist]}
        monkeypatch.setattr(fc.fp, "tidal_login", lambda: session)
        task_file = tmp_path / "t.txt"
        monkeypatch.setattr(fc.fp, "TASK_FILE", str(task_file))
        monkeypatch.setattr(fc.fp.time, "sleep", lambda *_: None)
        # Build kracht -> Aufgabenliste muss trotzdem geschrieben werden
        monkeypatch.setattr(fc.fp, "build_tidal_playlist",
                            mock.Mock(side_effect=RuntimeError("Plex weg")))

        cfg = self._minimal_cfg(lineup, target="tidal", dry_run=False)
        feed_inputs(monkeypatch, ["y", ""])
        fc.run_generation(cfg)
        assert task_file.exists()  # trotz Build-Crash geschrieben

    def test_task_liste_ueberlebt_abbruch_in_collect(self, tmp_path, monkeypatch):
        lineup = tmp_path / "l.txt"
        lineup.write_text("### Line-Up\nBand A\n", encoding="utf-8")
        session = mock.Mock()
        monkeypatch.setattr(fc.fp, "tidal_login", lambda: session)
        task_file = tmp_path / "t.txt"
        monkeypatch.setattr(fc.fp, "TASK_FILE", str(task_file))
        monkeypatch.setattr(fc.fp.time, "sleep", lambda *_: None)

        # collect() erfasst eine Aufgabe und bricht dann ab (z.B. Strg-C)
        def fake_collect(session, bands, genres, top, catalog, tasks, **kw):
            tasks.not_found_artist.append("Abgebrochene Band")
            raise KeyboardInterrupt
        monkeypatch.setattr(fc.fp, "collect", fake_collect)

        cfg = self._minimal_cfg(lineup, target="tidal", dry_run=False)
        feed_inputs(monkeypatch, ["y", ""])
        fc.run_generation(cfg)
        assert task_file.exists()
        assert "Abgebrochene Band" in task_file.read_text(encoding="utf-8")
