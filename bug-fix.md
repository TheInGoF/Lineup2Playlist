# Bug-Fix: Playlist-Upload bricht bei 412 ab (150 von 301 Tracks)

Stand 2026-07-05. ✅ GEFIXT in `build_tidal_playlist` (festival_playlist.py),
mit Tests abgedeckt. Details unten dokumentiert.

## Was umgesetzt wurde

- Bestehende Playlist gleichen Namens wird wiederverwendet
  (`_existing_playlist`); vorhandene Track-IDs werden paginiert ausgelesen
  (`_playlist_track_ids`, 100er-Seiten) und abgezogen → beim erneuten Lauf
  werden nur die fehlenden Tracks ergänzt, **nichts doppelt eingespeist**.
- 412-Retry mit frischem ETag (`_add_batch_with_retry`): bei 412 wird das
  Playlist-Objekt via `session.playlist(id)` neu geladen und der Block bis
  zu 3× wiederholt; 0,3 s Pause zwischen Blöcken.
- Bricht ein Block endgültig ab, wird sauber gemeldet statt zu crashen;
  ein erneuter Lauf ergänzt den Rest.
- **Auch 429 / Rate-Limit** wird jetzt mit Backoff wiederholt (tidalapi
  wickelt 429 in `TooManyRequests` ohne `.response`; `_ist_rate_limit`
  erkennt beides). Bei 300+ Tracks realistisch.
- Nicht behebbare Fehler werden nicht wiederholt, sondern sauber gemeldet.
- Tests: 412-einmalig→Retry, 412-dauerhaft→Abbruch, 429→Backoff-Retry,
  nicht-behebbar→kein Retry, Wiederverwendung ohne Duplikate, vollständige
  Playlist→kein add, Pagination, Namensabgleich.

---

## Ursprüngliche Analyse (zur Nachvollziehbarkeit)

## Symptom

Realer Lauf mit rodeo.txt: 301 Tracks gesammelt, Tidal-Playlist angelegt,
aber nur 150 Tracks hochgeladen. Dann:

    Fehler: HTTPError: 412 Client Error
    für url: https://api.tidal.com/v1/playlists/<id>/items?...

## Ursache (kein Tidal-Größenlimit!)

Tidal-Playlists dürfen tausende Tracks enthalten. 412 = "Precondition
Failed": Die tidalapi-Bibliothek schickt beim Hinzufügen einen ETag
(Versionskennung der Playlist) mit. `build_tidal_playlist` fügt in
50er-Blöcken hinzu; nach jedem Block ändert sich die Playlist-Version
serverseitig. Beim 4. Block (Track 151+) war der mitgeschickte ETag
veraltet -> Tidal lehnt ab. Bekanntes tidalapi-0.8.x-Problem bei schnell
aufeinanderfolgenden Batch-Adds.

## Fix-Plan für `build_tidal_playlist` (festival_playlist.py)

1. **Playlist wiederverwenden statt neu anlegen**: Vor dem Anlegen per
   `session.user.playlists()` prüfen, ob schon eine Playlist mit dem
   Ziel-Namen existiert. Falls ja: diese verwenden.
2. **Nur fehlende Tracks ergänzen (verhindert Duplikate beim Retry)**:
   Vorhandene Track-IDs der Playlist auslesen (`playlist.tracks()`,
   paginiert in 100er-Schritten!) und von der Upload-Liste abziehen.
   -> Beim erneuten Lauf werden die 150 vorhandenen übersprungen und
   nur die fehlenden 151 hochgeladen. NICHTS wird doppelt eingespeist.
3. **412-Retry mit frischem ETag**: Jeden 50er-Block bei 412 bis zu
   3x wiederholen; vor dem Retry das Playlist-Objekt neu laden
   (frischer ETag), dazu `time.sleep(1)`.
4. **Pause zwischen Blöcken** (`time.sleep(0.3)`), damit der ETag-Konflikt
   gar nicht erst entsteht.
5. Am Ende ausgeben: X neu hinzugefügt, Y waren schon vorhanden.

## Tests dazu (test_festival_playlist.py)

- Mock-Playlist wirft beim 2. `add()` einmal 412 -> Retry mit neu geladenem
  Objekt -> alle Blöcke kommen an.
- Existierende Playlist gleichen Namens mit 150 vorhandenen IDs ->
  nur die fehlenden IDs werden per `add()` geschickt.
- 3x 412 in Folge -> sauberer Abbruch, Aufgabenliste bleibt erhalten
  (schreibt die CLI schon heute, siehe finally in run_generation).

## Workaround bis zum Fix

Erneuter Lauf legt eine ZWEITE Playlist gleichen Namens an (Tidal erlaubt
Namensdubletten) — vorher die unvollständige Playlist in der Tidal-App
löschen, sonst hat man beide. Nichts wird in die alte doppelt eingespeist.
