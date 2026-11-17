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
        self.assertIn("log", str(ctx.exception))

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
        call = make_call({"music/in_progress_items": [AUDIOBOOK], "music/recently_played_items": [TRACK]})
        out = ma.home("http://h", "t", call=call)
        self.assertEqual(out["continue"][0]["name"], "Example Book")
        self.assertEqual(out["recent"][0]["name"], "Example Song")

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

    def test_no_match_returns_empty(self):
        call = make_call({"players/all": self.PLAYERS})
        with mock.patch("ma_backend.socket.gethostname", return_value="other"):
            self.assertEqual(ma.resolve_queue("h", "t", "", call=call), "")


class ControlTests(unittest.TestCase):
    def test_playpause(self):
        call = make_call({"player_queues/play_pause": None})
        ma.control("h", "t", "q1", "playpause", call=call)
        self.assertEqual(call.calls[0], ("player_queues/play_pause", {"queue_id": "q1"}))

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


if __name__ == "__main__":
    unittest.main()
