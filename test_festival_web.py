"""
Tests fuer festival_web.py — laufen offline (Tidal/Plex gemockt, Ephemeral-Port).

Ausfuehren:  .venv/bin/pytest -v
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import pytest

import festival_playlist as fp
import festival_web as w


# ---------------------------------------------------------------------------
# RunLog
# ---------------------------------------------------------------------------

class TestRunLog:
    def test_start_emit_finish(self):
        log = w.RunLog()
        log.start()
        assert log.active is True
        log.emit("log", "hallo")
        events, active = log.snapshot_from(0, timeout=0.1)
        assert active is True
        assert events == [{"type": "log", "text": "hallo"}]
        log.finish()
        assert log.active is False
        # nach finish gibt es ein done-Event
        events, active = log.snapshot_from(1, timeout=0.1)
        assert active is False
        assert events[-1] == {"type": "done", "text": "done"}

    def test_start_leert_alte_events(self):
        log = w.RunLog()
        log.emit("log", "alt")
        log.start()
        events, _ = log.snapshot_from(0, timeout=0.1)
        assert events == []


class TestStreamToLog:
    def test_zeilenweise_umleitung(self):
        log = w.RunLog()
        mirror = mock.Mock()
        s = w._StreamToLog(log, mirror)
        s.write("erste\nzweite\nunvollstaendig")
        events, _ = log.snapshot_from(0, timeout=0.1)
        assert [e["text"] for e in events] == ["erste", "zweite"]
        s.close_line()  # Rest ohne Newline
        events, _ = log.snapshot_from(0, timeout=0.1)
        assert events[-1]["text"] == "unvollstaendig"
        mirror.write.assert_called()  # spiegelt ins Terminal


class TestLineupInfo:
    def test_gueltige_datei(self):
        path = os.path.join(os.path.dirname(__file__), "example_lineup.txt")
        info = w._lineup_info(path)
        assert len(info["bands"]) == 10
        assert "punk" in info["genres"]

    def test_fehlender_pfad(self):
        assert w._lineup_info("/gibt/es/nicht.txt") is None
        assert w._lineup_info(None) is None


# ---------------------------------------------------------------------------
# HTTP-Endpunkte (echter Server auf Ephemeral-Port)
# ---------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path, monkeypatch):
    # Config/Task-Datei in tmp, Tidal-Zeit ausschalten
    monkeypatch.setattr(w.fc, "CONFIG_FILE", str(tmp_path / "cli.json"))
    monkeypatch.setattr(fp, "TASK_FILE", str(tmp_path / "tasks.txt"))
    monkeypatch.setattr(fp.time, "sleep", lambda *_: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), w.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def _post(base, path, obj):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestEndpoints:
    def test_state_liefert_config_und_lineups(self, server):
        status, data = _get(server, "/api/state")
        assert status == 200
        assert data["config"]["target"] == "tidal"
        assert "lineups" in data and "running" in data

    def test_index_html(self, server):
        with urllib.request.urlopen(server + "/", timeout=5) as r:
            body = r.read().decode()
        assert r.status == 200
        assert "Lineup2Playlist" in body

    def test_preview(self, server):
        path = os.path.join(os.path.dirname(__file__), "example_lineup.txt")
        status, data = _get(server, "/api/preview?lineup=" +
                            urllib.parse.quote(path))
        assert status == 200
        assert len(data["info"]["bands"]) == 10

    def test_run_ohne_lineup_gibt_400(self, server):
        status, data = _post(server, "/api/run", {"lineup": None})
        assert status == 400
        assert "line-up" in data["error"]

    def test_plex_ohne_creds_gibt_400(self, server):
        path = os.path.join(os.path.dirname(__file__), "example_lineup.txt")
        status, data = _post(server, "/api/run", {
            "lineup": path, "target": "plex", "dry_run": False,
            "plex_token": "DEIN_PLEX_TOKEN",
            "plex_baseurl": "http://192.168.x.x:32400"})
        assert status == 400
        assert "Plex" in data["error"]

    def test_dry_run_stream_bis_done(self, server, monkeypatch):
        # Tidal mocken -> vollstaendiger Dry-Run-Lauf ueber HTTP
        art = mock.Mock(); art.name = "Band"
        trk = mock.Mock(); trk.name = "Song"; trk.popularity = 5; trk.id = 1
        art.get_top_tracks.return_value = [trk]
        sess = mock.Mock(); sess.search.return_value = {"artists": [art]}
        monkeypatch.setattr(fp, "tidal_login", lambda: sess)
        path = os.path.join(os.path.dirname(__file__), "example_lineup.txt")

        status, data = _post(server, "/api/run", {
            "lineup": path, "target": "tidal", "dry_run": True,
            "top": 1, "name": "T"})
        assert status == 200 and data["ok"]

        events = []
        with urllib.request.urlopen(server + "/api/stream", timeout=8) as s:
            for raw in s:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    ev = json.loads(line[5:].strip())
                    events.append(ev)
                    if ev["type"] == "done":
                        break
        assert any(e["type"] == "done" for e in events)
        assert any("Dry run" in e["text"] for e in events)
