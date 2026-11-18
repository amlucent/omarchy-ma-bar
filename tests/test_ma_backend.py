import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import ma_backend as ma  # noqa: E402


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_call(table):
    """A fake api_call: `table` maps command -> result (or an Exception)."""
    calls = []

    def call(base, token, command, args=None):
        calls.append((command, args))
        value = table[command]
        if isinstance(value, Exception):
            raise value
        return value

    call.calls = calls
    return call


class NormalizeUrlTests(unittest.TestCase):
    def test_adds_scheme(self):
        self.assertEqual(ma.normalize_base_url("192.0.2.10:8095"), "http://192.0.2.10:8095")

    def test_keeps_https_and_drops_path(self):
        self.assertEqual(ma.normalize_base_url("https://music.example.com/api?x=1"),
                         "https://music.example.com")

    def test_rejects_credentials(self):
        with self.assertRaises(ma.MaError):
            ma.normalize_base_url("http://user:pw@host:8095")

    def test_rejects_other_schemes_and_empty(self):
        for bad in ("", "ftp://host", "file:///etc/passwd"):
            with self.assertRaises(ma.MaError):
                ma.normalize_base_url(bad)


class HttpTests(unittest.TestCase):
    def test_success(self):
        opener = mock.Mock(return_value=FakeResponse(b'{"a": 1}'))
        self.assertEqual(ma.api_call("http://h", "tok", "players/all", opener=opener), {"a": 1})
        req = opener.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        self.assertEqual(json.loads(req.data)["command"], "players/all")

    def test_bad_token_message(self):
        from urllib.error import HTTPError
        err = HTTPError("http://h/api", 401, "no", {}, None)
        opener = mock.Mock(side_effect=err)
        with self.assertRaises(ma.MaError) as ctx:
            ma.api_call("http://h", "tok", "x", opener=opener)
        self.assertIn("token", str(ctx.exception))
        self.assertNotIn("tok\n", str(ctx.exception))

    def test_server_error_is_explained(self):
        from urllib.error import HTTPError
        opener = mock.Mock(side_effect=HTTPError("http://h/api", 500, "boom", {}, None))
        with self.assertRaises(ma.MaError) as ctx:
            ma.api_call("http://h", "tok", "player_queues/play_media", opener=opener)
        self.assertIn("play", str(ctx.exception))
        self.assertIn("Spotify", str(ctx.exception))

    def test_server_error_on_a_lookup_does_not_blame_spotify(self):
        from urllib.error import HTTPError
        opener = mock.Mock(side_effect=HTTPError("http://h/api", 500, "boom", {}, None))
        with self.assertRaises(ma.MaError) as ctx:
            ma.api_call("http://h", "tok", "music/audiobooks/get", opener=opener)
        self.assertNotIn("Spotify", str(ctx.exception))

    def test_unreachable(self):
        from urllib.error import URLError
        opener = mock.Mock(side_effect=URLError("down"))
        with self.assertRaises(ma.MaError):
            ma.api_call("http://h", "tok", "x", opener=opener)

    def test_oversized_declared(self):
        resp = FakeResponse(b"{}", {"Content-Length": str(ma.MAX_BYTES + 1)})
        with self.assertRaises(ma.MaError):
            ma.read_limited(resp)

    def test_oversized_streamed(self):
        resp = FakeResponse(b"x" * 200)
        with self.assertRaises(ma.MaError):
            ma.read_limited(resp, limit=100)

    def test_garbage_json(self):
        opener = mock.Mock(return_value=FakeResponse(b"<html>"))
        with self.assertRaises(ma.MaError):
            ma.api_call("http://h", "tok", "x", opener=opener)


AUDIOBOOK = {
    "media_type": "audiobook", "uri": "library://audiobook/1", "name": "Example Book",
    "duration": 3600, "authors": [{"name": "A. Writer"}, {"name": "B. Author"}],
    "image": {"type": "thumb", "path": "https://abs.example/cover?token=SECRET", "provider": "abs",
              "proxy_id": "abc123"},
}
TRACK = {
    "media_type": "track", "uri": "library://track/2", "name": "Example Song", "duration": 200,
    "artists": [{"name": "The Band"}], "metadata": {"images": [{"path": "p.jpg", "provider": "fs", "proxy_id": "def456"}]},
}


class SimplifyTests(unittest.TestCase):
    def test_audiobook(self):
        item = ma.simplify(AUDIOBOOK, "http://h:8095")
        self.assertEqual(item["subtitle"], "A. Writer, B. Author")
        self.assertEqual(item["type"], "audiobook")
        self.assertEqual(item["cover"], "http://h:8095/imageproxy/abc123?size=256&fmt=jpg")

    def test_raw_artwork_address_never_leaks(self):
        # Some providers embed an access token in the artwork address.
        out = json.dumps([ma.simplify(AUDIOBOOK, "http://h:8095"),
                          ma.simplify_queue({"current_item": {"name": "x", "media_item": AUDIOBOOK}},
                                            "http://h:8095")])
        self.assertNotIn("SECRET", out)
        self.assertNotIn("abs.example", out)

    def test_resume_and_played(self):
        item = ma.simplify(dict(AUDIOBOOK, resume_position_ms=90000, fully_played=True))
        self.assertEqual(item["resume"], 90.0)
        self.assertTrue(item["played"])
        self.assertIsNone(ma.simplify(AUDIOBOOK)["resume"])

    def test_track_falls_back_to_metadata_image(self):
        item = ma.simplify(TRACK, "http://h")
        self.assertEqual(item["subtitle"], "The Band")
        self.assertEqual(item["cover"], "http://h/imageproxy/def456?size=256&fmt=jpg")

    def test_no_cover_when_no_proxy_id(self):
        self.assertEqual(ma.simplify({"media_type": "track", "image": {"path": "x"}}, "http://h")["cover"], "")

    def test_cover_size_is_restricted_to_what_the_server_allows(self):
        ref = {"proxy_id": "z"}
        self.assertIn("size=256", ma.cover_url("http://h", ref, 128))
        self.assertIn("size=512", ma.cover_url("http://h", ref, 512))

    def test_junk_is_dropped(self):
        self.assertIsNone(ma.simplify("nope"))
        self.assertEqual(ma.simplify({})["name"], "")

    def test_queue_idle(self):
        self.assertEqual(ma.simplify_queue({"state": "idle", "current_item": None})["item"], None)

    def test_queue_playing(self):
        q = ma.simplify_queue({
            "queue_id": "q1", "state": "playing", "elapsed_time": 12.5, "playback_speed": 1.25,
            "current_item": {"name": "Example Song", "duration": 200, "media_item": TRACK},
        })
        self.assertEqual(q["state"], "playing")
        self.assertEqual(q["item"]["subtitle"], "The Band")
        self.assertEqual(q["speed"], 1.25)


class EpisodeAndHomeTests(unittest.TestCase):
    def test_split_uri(self):
        self.assertEqual(ma.split_uri("library://podcast/12"), ("library", "12"))
        self.assertEqual(ma.split_uri("garbage"), ("", ""))
        self.assertEqual(ma.split_uri(""), ("", ""))

    def test_episodes_calls_with_provider_and_id(self):
        call = make_call({"music/podcasts/podcast_episodes": [
            {"media_type": "podcast_episode", "uri": "u1", "name": "Ep 1", "duration": 60,
             "podcast": {"name": "Show"}, "fully_played": False}]})
        out = ma.episodes("http://h", "t", "library://podcast/12", call=call)
        self.assertEqual(out[0]["subtitle"], "Show")
        self.assertEqual(call.calls[0][1], {"item_id": "12", "provider_instance_id_or_domain": "library"})

    def test_episodes_rejects_bad_uri(self):
        with self.assertRaises(ma.MaError):
            ma.episodes("http://h", "t", "nope", call=make_call({}))

    def test_home_has_both_sections(self):
        call = make_call({"music/in_progress_items": [AUDIOBOOK], "music/recently_played_items": [TRACK],
                          "music/audiobooks/get": dict(AUDIOBOOK, resume_position_ms=1800000, duration=7200)})
        out = ma.home("http://h", "t", call=call)
        self.assertEqual(out["continue"][0]["name"], "Example Book")
        self.assertEqual(out["continue"][0]["resume"], 1800.0)
        self.assertEqual(out["continue"][0]["duration"], 7200)
        self.assertEqual(out["recent"][0]["name"], "Example Song")

    def test_home_survives_a_failed_detail_lookup(self):
        call = make_call({"music/in_progress_items": [AUDIOBOOK], "music/recently_played_items": [],
                          "music/audiobooks/get": ma.MaError("boom")})
        out = ma.home("http://h", "t", call=call)
        self.assertEqual(out["continue"][0]["name"], "Example Book")
        self.assertIsNone(out["continue"][0]["resume"])

    def test_now_full_adds_volume(self):
        call = make_call({
            "player_queues/get": {"queue_id": "q", "state": "playing", "current_item": None},
            "players/get": {"volume_level": 30, "volume_muted": False, "display_name": "Desk"}})
        out = ma.now_playing("http://h", "t", "q", full=True, call=call)
        self.assertEqual((out["volume"], out["muted"], out["player"]), (30, False, "Desk"))

    def test_now_basic_skips_player_call(self):
        call = make_call({"player_queues/get": {"queue_id": "q", "state": "idle", "current_item": None}})
        ma.now_playing("http://h", "t", "q", call=call)
        self.assertEqual([c[0] for c in call.calls], ["player_queues/get"])


QUEUE_ITEMS = [
    {"queue_item_id": "a1", "index": 0, "name": "First", "duration": 100, "media_item": TRACK},
    {"queue_item_id": "b2", "index": 0, "name": "Second", "duration": 200, "media_item": AUDIOBOOK},
    {"queue_item_id": "c3", "index": 0, "name": "Third", "duration": 300, "media_item": {}},
]


class QueueTests(unittest.TestCase):
    def test_positions_come_from_the_list_not_the_items_index_field(self):
        call = make_call({"player_queues/get": {"current_index": 1}, "player_queues/items": QUEUE_ITEMS})
        out = ma.queue_items("http://h", "t", "q", call=call)
        self.assertEqual([i["index"] for i in out], [0, 1, 2])
        self.assertEqual([i["current"] for i in out], [False, True, False])
        self.assertEqual([i["id"] for i in out], ["a1", "b2", "c3"])
        self.assertEqual(out[0]["subtitle"], "The Band")

    def test_no_current_item(self):
        call = make_call({"player_queues/get": {"current_index": None}, "player_queues/items": QUEUE_ITEMS})
        self.assertFalse(any(i["current"] for i in ma.queue_items("h", "t", "q", call=call)))

    def test_empty_queue(self):
        call = make_call({"player_queues/get": {}, "player_queues/items": []})
        self.assertEqual(ma.queue_items("h", "t", "q", call=call), [])

    def test_enqueue_modes(self):
        call = make_call({"player_queues/play_media": None})
        ma.enqueue("h", "t", "q", "next", "library://track/1", call=call)
        ma.enqueue("h", "t", "q", "add", "library://track/2", call=call)
        self.assertEqual(call.calls[0][1], {"queue_id": "q", "media": "library://track/1", "option": "next"})
        self.assertEqual(call.calls[1][1]["option"], "add")
        with self.assertRaises(ma.MaError):
            ma.enqueue("h", "t", "q", "bogus", "u", call=call)
        with self.assertRaises(ma.MaError):
            ma.enqueue("h", "t", "q", "add", "", call=call)

    def test_remove_and_clear(self):
        call = make_call({"player_queues/delete_item": None, "player_queues/clear": None})
        ma.queue_remove("h", "t", "q", "b2", call=call)
        ma.queue_clear("h", "t", "q", call=call)
        self.assertEqual(call.calls[0][1], {"queue_id": "q", "item_id_or_index": "b2"})
        self.assertEqual(call.calls[1][0], "player_queues/clear")
        with self.assertRaises(ma.MaError):
            ma.queue_remove("h", "t", "q", "", call=call)

    def test_move_direction_and_zero(self):
        call = make_call({"player_queues/move_item": None})
        ma.queue_move("h", "t", "q", "b2", "-1", call=call)   # negative = up
        ma.queue_move("h", "t", "q", "b2", 0, call=call)      # nothing to do
        self.assertEqual(len(call.calls), 1)
        self.assertEqual(call.calls[0][1]["pos_shift"], -1)

    def test_jump_by_position_or_id(self):
        call = make_call({"player_queues/play_index": None})
        ma.queue_jump("h", "t", "q", "2", call=call)
        ma.queue_jump("h", "t", "q", "c3", call=call)
        self.assertEqual(call.calls[0][1]["index"], 2)
        self.assertEqual(call.calls[1][1]["index"], "c3")
        with self.assertRaises(ma.MaError):
            ma.queue_jump("h", "t", "q", "", call=call)


BOOK_FULL = dict(AUDIOBOOK, resume_position_ms=15179000, duration=84845, fully_played=False,
                 provider_mappings=[{"provider_domain": "audiobookshelf", "item_id": "abs-123"}],
                 metadata={"chapters": [{"position": 1, "name": "One", "start": 0.0, "end": 100.0},
                                        {"position": 2, "name": "Two", "start": 100.0, "end": None},
                                        {"name": "broken"}]})


class DetailTests(unittest.TestCase):
    def test_split_uri_keeps_provider_case(self):
        self.assertEqual(ma.split_uri("audiobookshelf--rcEcp4Jb://podcast_episode/abc def"),
                         ("audiobookshelf--rcEcp4Jb", "abc def"))

    def test_details(self):
        call = make_call({"music/audiobooks/get": BOOK_FULL})
        out = ma.details("h", "t", "audiobook", "library://audiobook/9", call=call)
        self.assertEqual(out["resume"], 15179.0)
        self.assertEqual(out["abs_id"], "abs-123")
        self.assertEqual([c["name"] for c in out["chapters"]], ["One", "Two"])  # malformed one dropped
        self.assertEqual(call.calls[0][1], {"item_id": "9", "provider_instance_id_or_domain": "library"})

    def test_details_rejects_unsupported_type(self):
        with self.assertRaises(ma.MaError):
            ma.details("h", "t", "track", "library://track/1", call=make_call({}))

    def test_abs_item_id_absent(self):
        self.assertEqual(ma.abs_item_id({"provider_mappings": [{"provider_domain": "plex", "item_id": "x"}]}), "")

    def test_fmt_time(self):
        self.assertEqual(ma.fmt_time(75), "1:15")
        self.assertEqual(ma.fmt_time(3725), "1:02:05")
        self.assertEqual(ma.fmt_time(-5), "0:00")


class FakeAbs:
    """Stands in for urlopen against an Audiobookshelf server."""
    def __init__(self, me=None):
        self.requests = []
        self.me = me if me is not None else {"bookmarks": []}

    def __call__(self, req, timeout=None):
        self.requests.append((req.get_method(), req.full_url, req.data, req.get_header("Authorization")))
        if req.get_method() == "GET":
            return FakeResponse(json.dumps(self.me).encode())
        return FakeResponse(b"{}")


class AbsBookmarkTests(unittest.TestCase):
    ME = {"bookmarks": [
        {"libraryItemId": "abs-123", "time": 900, "title": "Later", "createdAt": 2},
        {"libraryItemId": "other", "time": 5, "title": "Not this book", "createdAt": 3},
        {"libraryItemId": "abs-123", "time": 60, "title": "Early", "createdAt": 1},
    ]}

    def test_filters_to_the_book_and_sorts_by_time(self):
        out = ma.abs_bookmarks("http://abs", "tok", "abs-123", opener=FakeAbs(self.ME))
        self.assertEqual([(b["time"], b["title"]) for b in out], [(60, "Early"), (900, "Later")])

    def test_sends_bearer_token(self):
        fake = FakeAbs(self.ME)
        ma.abs_bookmarks("http://abs", "tok", "abs-123", opener=fake)
        self.assertEqual(fake.requests[0][3], "Bearer tok")
        self.assertEqual(fake.requests[0][1], "http://abs/api/me")

    def test_add_defaults_title_and_sends_integer_seconds(self):
        fake = FakeAbs()
        out = ma.abs_add_bookmark("http://abs", "tok", "abs-123", "3725.8", opener=fake)
        method, url, data, _auth = fake.requests[0]
        self.assertEqual((method, url), ("POST", "http://abs/api/me/item/abs-123/bookmark"))
        self.assertEqual(json.loads(data), {"time": 3725, "title": "Bookmark @ 1:02:05"})
        self.assertEqual(out["title"], "Bookmark @ 1:02:05")

    def test_add_keeps_a_given_title(self):
        fake = FakeAbs()
        ma.abs_add_bookmark("http://abs", "tok", "abs-123", 10, "  The twist  ", opener=fake)
        self.assertEqual(json.loads(fake.requests[0][2])["title"], "The twist")

    def test_delete(self):
        fake = FakeAbs()
        ma.abs_delete_bookmark("http://abs", "tok", "abs-123", "60", opener=fake)
        self.assertEqual(fake.requests[0][:2], ("DELETE", "http://abs/api/me/item/abs-123/bookmark/60"))

    def test_item_id_is_url_quoted(self):
        fake = FakeAbs()
        ma.abs_delete_bookmark("http://abs", "tok", "a/b", 1, opener=fake)
        self.assertIn("/item/a%2Fb/", fake.requests[0][1])

    def test_plain_text_reply_to_a_write_is_fine(self):
        opener = mock.Mock(return_value=FakeResponse(b"OK"))
        self.assertIsNone(ma.abs_call("http://abs", "tok", "DELETE", "/x", opener=opener))

    def test_plain_text_reply_to_a_read_is_an_error(self):
        opener = mock.Mock(return_value=FakeResponse(b"<html>"))
        with self.assertRaises(ma.MaError):
            ma.abs_call("http://abs", "tok", "GET", "/api/me", opener=opener)

    def test_errors_are_explained(self):
        from urllib.error import HTTPError
        for code, word in ((401, "token"), (404, "find"), (500, "error")):
            opener = mock.Mock(side_effect=HTTPError("u", code, "x", {}, None))
            with self.assertRaises(ma.MaError) as ctx:
                ma.abs_call("http://abs", "tok", "GET", "/api/me", opener=opener)
            self.assertIn(word, str(ctx.exception))


class GoToTests(unittest.TestCase):
    def make(self, current_sequence):
        calls, seq = [], list(current_sequence)

        def call(base, token, command, args=None):
            calls.append((command, args))
            if command == "player_queues/get":
                uri = seq.pop(0) if len(seq) > 1 else seq[0]
                return {"current_item": {"media_item": {"uri": uri}}}
            return None
        call.calls = calls
        return call

    def test_already_playing_just_seeks(self):
        call = self.make(["u1"])
        ma.go_to("h", "t", "q", "u1", 120.7, call=call, sleep=lambda s: None)
        cmds = [c[0] for c in call.calls]
        self.assertNotIn("player_queues/play_media", cmds)
        self.assertEqual(call.calls[-1], ("player_queues/seek", {"queue_id": "q", "position": 120}))

    def test_starts_the_item_then_seeks(self):
        call = self.make(["other", "other", "u1"])
        ma.go_to("h", "t", "q", "u1", 30, call=call, sleep=lambda s: None)
        cmds = [c[0] for c in call.calls]
        self.assertLess(cmds.index("player_queues/play_media"), cmds.index("player_queues/seek"))

    def test_gives_up_if_it_never_starts(self):
        call = self.make(["other"])
        with self.assertRaises(ma.MaError):
            ma.go_to("h", "t", "q", "u1", 30, call=call, sleep=lambda s: None, tries=3)
        self.assertNotIn("player_queues/seek", [c[0] for c in call.calls])


class QueryTests(unittest.TestCase):
    def test_search_groups_and_skips_empty(self):
        call = make_call({"music/search": {"tracks": [TRACK], "albums": [], "audiobooks": [AUDIOBOOK],
                                           "genres": [], "radio": []}})
        out = ma.search("http://h", "t", "x", call=call)
        self.assertEqual(sorted(out), ["audiobooks", "tracks"])
        self.assertEqual(call.calls[0][1]["search_query"], "x")

    def test_library_validates_kind(self):
        with self.assertRaises(ma.MaError):
            ma.library("http://h", "t", "bogus", call=make_call({}))

    def test_library_passes_paging(self):
        call = make_call({"music/audiobooks/library_items": [AUDIOBOOK]})
        out = ma.library("http://h", "t", "audiobook", 10, 20, "mur", call=call)
        self.assertEqual(out[0]["name"], "Example Book")
        args = call.calls[0][1]
        self.assertEqual((args["limit"], args["offset"], args["search"]), (10, 20, "mur"))

    def test_players(self):
        call = make_call({"players/all": [
            {"player_id": "p1", "display_name": "Kitchen", "provider": "sonos", "available": True,
             "playback_state": "idle", "volume_level": 30}]})
        self.assertEqual(ma.list_players("http://h", "t", call=call)[0]["name"], "Kitchen")


class ResolveQueueTests(unittest.TestCase):
    PLAYERS = [
        {"player_id": "s1", "display_name": "Kitchen", "provider": "sonos", "available": True},
        {"player_id": "c1", "display_name": "mybox.lan", "provider": "sendspin", "available": True},
        {"player_id": "c2", "display_name": "mybox.lan", "provider": "sendspin", "available": False},
    ]

    def test_configured_wins(self):
        self.assertEqual(ma.resolve_queue("h", "t", "chosen", call=make_call({})), "chosen")

    def test_matches_this_host(self):
        call = make_call({"players/all": self.PLAYERS})
        with mock.patch("ma_backend.socket.gethostname", return_value="mybox"):
            self.assertEqual(ma.resolve_queue("h", "t", "", call=call), "c1")

    def test_matches_a_universal_player_named_after_this_host(self):
        players = [{"player_id": "u1", "display_name": "mybox", "provider": "universal_player", "available": True}]
        with mock.patch("ma_backend.socket.gethostname", return_value="mybox"):
            self.assertEqual(ma.resolve_queue("h", "t", "", call=make_call({"players/all": players})), "u1")

    def test_no_match_returns_empty(self):
        call = make_call({"players/all": self.PLAYERS})
        with mock.patch("ma_backend.socket.gethostname", return_value="other"):
            self.assertEqual(ma.resolve_queue("h", "t", "", call=call), "")


class ControlTests(unittest.TestCase):
    def test_playpause(self):
        call = make_call({"player_queues/play_pause": None})
        ma.control("h", "t", "q1", "playpause", call=call)
        self.assertEqual(call.calls[0], ("player_queues/play_pause", {"queue_id": "q1"}))

    def test_pause_is_idempotent_not_toggle(self):
        call = make_call({"player_queues/pause": None})
        ma.control("h", "t", "q1", "pause", call=call)
        self.assertEqual(call.calls[0], ("player_queues/pause", {"queue_id": "q1"}))

    def test_seek_and_speed(self):
        call = make_call({"player_queues/seek": None, "player_queues/set_playback_speed": None})
        ma.control("h", "t", "q1", "seek", ["42.9"], call=call)
        ma.control("h", "t", "q1", "speed", ["1.5"], call=call)
        self.assertEqual(call.calls[0][1]["position"], 42)
        self.assertEqual(call.calls[1][1]["speed"], 1.5)

    def test_volume_is_clamped(self):
        call = make_call({"players/cmd/volume_set": None})
        ma.control("h", "t", "p1", "volume", ["250"], call=call)
        self.assertEqual(call.calls[0][1], {"player_id": "p1", "volume_level": 100})

    def test_bad_input(self):
        call = make_call({})
        with self.assertRaises(ma.MaError):
            ma.control("h", "t", "q", "seek", ["abc"], call=call)
        with self.assertRaises(ma.MaError):
            ma.control("h", "t", "q", "explode", call=call)

    def test_play_requires_uri(self):
        with self.assertRaises(ma.MaError):
            ma.play("h", "t", "q", "", call=make_call({}))
        call = make_call({"player_queues/play_media": None})
        ma.play("h", "t", "q1", "library://audiobook/1", call=call)
        self.assertEqual(call.calls[0][1], {"queue_id": "q1", "media": "library://audiobook/1"})


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_mode(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "config.json")
            ma.save_config({"serverUrl": "http://h:1"}, path)
            self.assertEqual(ma.load_config(path), {"serverUrl": "http://h:1"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_missing_or_corrupt_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ma.load_config(os.path.join(d, "nope.json")), {})
            bad = os.path.join(d, "bad.json")
            open(bad, "w").write("{not json")
            self.assertEqual(ma.load_config(bad), {})

    def test_token_never_in_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            ma.save_config({"serverUrl": "http://h"}, path)
            self.assertNotIn("token", open(path).read().lower())


class KeyringTests(unittest.TestCase):
    def test_token_goes_on_stdin_not_argv(self):
        with mock.patch("ma_backend.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            ma.store_token("s3cret")
        argv = run.call_args[0][0]
        self.assertNotIn("s3cret", " ".join(argv))
        self.assertEqual(run.call_args[1]["input"], "s3cret")

    def test_env_override_for_dev(self):
        with mock.patch.dict(os.environ, {"MA_PLUGIN_TOKEN": " abc "}):
            self.assertEqual(ma.load_token(), "abc")



def raw_player(pid, name, **kw):
    base = {"player_id": pid, "display_name": name, "provider": "sonos", "type": "player",
            "available": True, "playback_state": "idle", "volume_level": 20,
            "group_childs": [], "synced_to": None, "active_group": None,
            "can_group_with": [], "supported_features": ["set_members", "volume_set"]}
    base.update(kw)
    return base


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.office = raw_player("o", "Office", group_childs=["o", "d"], can_group_with=["d", "k", "x"])
        self.desk = raw_player("d", "Desk", provider="airplay", synced_to="o")
        self.kitchen = raw_player("k", "Kitchen")
        self.off = raw_player("x", "Offline", available=False)
        self.table = {"providers": [{"domain": "universal_group", "available": True}],
                      "players/all": [self.desk, self.kitchen, self.off, self.office,
                                       raw_player("g", "Group", type="group")]}

    def test_leader_of_child_is_its_parent(self):
        call = make_call({"players/get": self.desk})
        # the fake returns the same player for both lookups; the id comes from synced_to
        self.assertEqual(ma.leader_of("h", "t", "d", call=call)[0], "o")

    def test_leader_of_unknown_player_falls_back(self):
        call = make_call({"players/get": ma.MaError("gone")})
        self.assertEqual(ma.leader_of("h", "t", "d", call=call), ("d", None))

    def test_now_follows_the_leader_queue(self):
        table = {"players/get": self.office, "player_queues/get": {"queue_id": "o", "state": "playing", "current_item": None}}
        call = make_call(table)
        out = ma.now_for_player("h", "t", "o", full=True, call=call)
        self.assertEqual((out["leader"], out["groupSize"], out["player"]), ("o", 2, "Office"))

    def test_speakers_lists_members_first_and_joinable(self):
        out = ma.speakers("h", "t", "d", call=make_call(self.table))
        self.assertEqual(out["leader"], "o")
        names = [(r["name"], r["member"], r["leader"]) for r in out["speakers"]]
        self.assertEqual(names, [("Office", True, True), ("Desk", True, False), ("Kitchen", False, False)])

    def test_speakers_skips_groups_and_unavailable(self):
        out = ma.speakers("h", "t", "o", call=make_call(self.table))
        self.assertFalse({"g", "x"} & {r["id"] for r in out["speakers"]})

    def test_group_add_and_remove(self):
        call = make_call({"players/get": self.office, "players/cmd/set_members": None})
        ma.group_members("h", "t", "o", add=["k"], call=call)
        self.assertEqual(call.calls[-1][1], {"target_player": "o", "player_ids_to_add": ["k"], "player_ids_to_remove": None})
        ma.group_members("h", "t", "o", remove=["d"], call=call)
        self.assertEqual(call.calls[-1][1]["player_ids_to_remove"], ["d"])

    def test_cannot_remove_the_leader(self):
        call = make_call({"players/get": self.office})
        with self.assertRaises(ma.MaError):
            ma.group_members("h", "t", "o", remove=["o"], call=call)

    def test_ungroup_removes_every_child(self):
        call = make_call({"players/get": self.office, "players/cmd/set_members": None})
        ma.ungroup("h", "t", "o", call=call)
        self.assertEqual(call.calls[-1][1]["player_ids_to_remove"], ["d"])

    def test_volume_is_clamped(self):
        call = make_call({"players/cmd/volume_set": None})
        ma.set_player_volume("h", "t", "o", 250, call=call)
        self.assertEqual(call.calls[0][1]["volume_level"], 100)


class SavedGroupTests(unittest.TestCase):
    def test_save_creates_a_sync_group_after_dissolving_the_adhoc_one(self):
        office = raw_player("o", "Office", group_childs=["o", "d"])
        call = make_call({"players/get": office, "players/cmd/set_members": None,
                          "players/create_group_player": {"player_id": "syncgroup_1"}})
        out = ma.save_group("h", "t", "o", "  Work  ", ["o", "d", "d"], call=call)
        self.assertEqual(out["id"], "syncgroup_1")
        cmds = [c[0] for c in call.calls]
        self.assertLess(cmds.index("players/cmd/set_members"), cmds.index("players/create_group_player"))
        self.assertEqual(call.calls[-1][1], {"provider": "sync_group", "name": "Work",
                                              "members": ["o", "d"], "dynamic": True})

    def test_mixed_brand_groups_use_the_universal_provider(self):
        call = make_call({"players/get": raw_player("o", "Office"),
                          "players/create_group_player": {"player_id": "g"}})
        ma.save_group("h", "t", "o", "Mix", ["a", "b"], provider="universal_group", call=call)
        self.assertEqual(call.calls[-1][1]["provider"], "universal_group")
        with self.assertRaises(ma.MaError):
            ma.save_group("h", "t", "o", "Bad", ["a", "b"], provider="evil", call=call)

    def test_save_validates(self):
        call = make_call({})
        with self.assertRaises(ma.MaError):
            ma.save_group("h", "t", "o", "", ["a", "b"], call=call)
        with self.assertRaises(ma.MaError):
            ma.save_group("h", "t", "o", "Solo", ["a"], call=call)

    def test_delete_refuses_real_speakers(self):
        call = make_call({"players/get": raw_player("o", "Office")})
        with self.assertRaises(ma.MaError):
            ma.delete_group("h", "t", "o", call=call)
        self.assertNotIn("players/remove_group_player", [c[0] for c in call.calls])

    def test_delete_removes_a_group(self):
        call = make_call({"players/get": raw_player("g", "Work", type="group"), "players/remove_group_player": None})
        ma.delete_group("h", "t", "g", call=call)
        self.assertEqual(call.calls[-1], ("players/remove_group_player", {"player_id": "g"}))

    def test_group_player_size_excludes_itself(self):
        group = raw_player("g", "Work", type="group", group_childs=["o", "d"])
        call = make_call({"players/get": group, "player_queues/get": {"queue_id": "g", "state": "idle", "current_item": None}})
        self.assertEqual(ma.now_for_player("h", "t", "g", call=call)["groupSize"], 2)



EPISODE_OLD = {"media_type": "podcast_episode", "name": "Old", "uri": "p://podcast_episode/1", "position": 1700000000000,
               "podcast": {"name": "Show A"}, "duration": 100}
EPISODE_NEW = {"media_type": "podcast_episode", "name": "New", "uri": "p://podcast_episode/2", "position": 1790000000000,
               "podcast": {"name": "Show B"}, "duration": 200}


class PodcastsHomeTests(unittest.TestCase):
    def table(self, in_progress=()):
        def call(base, token, command, args=None):
            if command == "music/podcasts/library_items":
                return [{"media_type": "podcast", "name": "A", "uri": "library://podcast/1"},
                        {"media_type": "podcast", "name": "B", "uri": "library://podcast/2"}]
            if command == "music/podcasts/podcast_episodes":
                return [EPISODE_OLD] if args["item_id"] == "1" else [EPISODE_NEW]
            if command == "music/in_progress_items":
                return list(in_progress)
            if command == "music/podcasts/podcast_episode":
                raise ma.MaError("no detail")
            raise AssertionError(command)
        return call

    def test_newest_first_across_shows(self):
        out = ma.podcasts_home("h", "t", call=self.table())
        self.assertEqual([i["name"] for i in out["newest"]], ["New", "Old"])
        self.assertEqual([s["name"] for s in out["shows"]], ["A", "B"])
        self.assertEqual(out["newest"][0]["published"], 1790000000)

    def test_unfinished_are_listed_once_and_only_episodes(self):
        started = dict(EPISODE_OLD, resume_position_ms=30000)
        out = ma.podcasts_home("h", "t", call=self.table([started, AUDIOBOOK]))
        self.assertEqual([i["name"] for i in out["continue"]], ["Old"])
        self.assertEqual([i["name"] for i in out["newest"]], ["New"])

    def test_one_broken_show_does_not_empty_the_tab(self):
        base = self.table()

        def call(b, t, command, args=None):
            if command == "music/podcasts/podcast_episodes" and args["item_id"] == "1":
                raise ma.MaError("feed down")
            return base(b, t, command, args)
        out = ma.podcasts_home("h", "t", call=call)
        self.assertEqual([i["name"] for i in out["newest"]], ["New"])


class MarkPlayedTests(unittest.TestCase):
    def test_mark_played_sends_the_full_item(self):
        item = {"item_id": "1", "duration": 321}
        call = make_call({"music/podcasts/podcast_episode": item, "music/mark_played": None})
        ma.mark_played("h", "t", "podcast_episode", "library://podcast_episode/1", True, call=call)
        command, args = call.calls[-1]
        self.assertEqual(command, "music/mark_played")
        self.assertEqual((args["media_item"], args["fully_played"], args["seconds_played"]), (item, True, 321))

    def test_unplayed_also_resets_audiobookshelf(self):
        item = {"item_id": "x", "provider_mappings": [{"provider_domain": "audiobookshelf", "item_id": "lib1 ep2"}]}
        call = make_call({"music/podcasts/podcast_episode": item, "music/mark_unplayed": None})
        sent = []
        ma.mark_played("h", "t", "podcast_episode", "p://podcast_episode/x", False, call=call,
                       abs_ctx=lambda: ("http://abs", "tok"),
                       abs_request=lambda base, tok, method, path, body: sent.append((method, path, body)))
        self.assertEqual(sent, [("PATCH", "/api/me/progress/lib1/ep2",
                                 {"isFinished": False, "progress": 0, "currentTime": 0})])

    def test_unplayed_needs_audiobookshelf_for_its_items(self):
        item = {"provider_mappings": [{"provider_domain": "audiobookshelf", "item_id": "lib1"}]}
        call = make_call({"music/audiobooks/get": item, "music/mark_unplayed": None})

        def not_connected():
            raise ma.MaError("connect Audiobookshelf in the settings to use bookmarks")
        with self.assertRaises(ma.MaError):
            ma.mark_played("h", "t", "audiobook", "library://audiobook/1", False, call=call, abs_ctx=not_connected)

    def test_mark_unplayed(self):
        call = make_call({"music/audiobooks/get": {"item_id": "1"}, "music/mark_unplayed": None})
        ma.mark_played("h", "t", "audiobook", "library://audiobook/1", False, call=call)
        self.assertEqual(call.calls[-1][0], "music/mark_unplayed")

    def test_other_types_are_refused(self):
        with self.assertRaises(ma.MaError):
            ma.mark_played("h", "t", "album", "library://album/1", True, call=make_call({}))



class ShuffleRepeatFavoriteTests(unittest.TestCase):
    def test_shuffle_and_repeat_controls(self):
        call = make_call({"player_queues/shuffle": None, "player_queues/repeat": None})
        ma.control("h", "t", "q1", "shuffle", ["1"], call=call)
        ma.control("h", "t", "q1", "shuffle", ["0"], call=call)
        ma.control("h", "t", "q1", "repeat", ["all"], call=call)
        self.assertEqual(call.calls[0], ("player_queues/shuffle", {"shuffle_enabled": True, "queue_id": "q1"}))
        self.assertEqual(call.calls[1][1]["shuffle_enabled"], False)
        self.assertEqual(call.calls[2], ("player_queues/repeat", {"repeat_mode": "all", "queue_id": "q1"}))

    def test_repeat_rejects_unknown_modes(self):
        with self.assertRaises(ma.MaError):
            ma.control("h", "t", "q1", "repeat", ["forever"], call=make_call({}))

    def test_transfer(self):
        call = make_call({"player_queues/transfer": None})
        ma.transfer_queue("h", "t", "a", "b", call=call)
        self.assertEqual(call.calls[0], ("player_queues/transfer",
                                         {"source_queue_id": "a", "target_queue_id": "b", "auto_play": True}))
        with self.assertRaises(ma.MaError):
            ma.transfer_queue("h", "t", "a", "a", call=call)

    def test_favorite_add_and_remove(self):
        call = make_call({"music/favorites/add_item": None, "music/favorites/remove_item": None})
        ma.favorite_set("h", "t", "track", "library://track/12", True, call=call)
        self.assertEqual(call.calls[-1], ("music/favorites/add_item", {"item": "library://track/12"}))
        ma.favorite_set("h", "t", "track", "library://track/12", False, call=call)
        self.assertEqual(call.calls[-1][1], {"media_type": "track", "library_item_id": 12})

    def test_favorite_remove_needs_a_library_item(self):
        with self.assertRaises(ma.MaError):
            ma.favorite_set("h", "t", "track", "plex://track/9", False, call=make_call({}))
        with self.assertRaises(ma.MaError):
            ma.favorite_set("h", "t", "player", "library://player/1", True, call=make_call({}))

    def test_favorites_collects_sections_and_survives_one_failure(self):
        def call(base, token, command, args=None):
            if command == "music/albums/library_items":
                raise ma.MaError("boom")
            if command == "music/tracks/library_items":
                assert args["favorite"] is True
                return [dict(TRACK, favorite=True)]
            return []
        out = ma.favorites("h", "t", call=call)
        self.assertEqual(list(out), ["tracks"])
        self.assertTrue(out["tracks"][0]["favorite"])


if __name__ == "__main__":
    unittest.main()
