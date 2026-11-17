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
    "image": {"type": "thumb", "path": "https://abs.example/cover?token=SECRET", "provider": "abs"},
}
TRACK = {
    "media_type": "track", "uri": "library://track/2", "name": "Example Song", "duration": 200,
    "artists": [{"name": "The Band"}], "metadata": {"images": [{"path": "p.jpg", "provider": "fs"}]},
}


class SimplifyTests(unittest.TestCase):
    def test_audiobook(self):
        item = ma.simplify(AUDIOBOOK)
        self.assertEqual(item["subtitle"], "A. Writer, B. Author")
        self.assertEqual(item["type"], "audiobook")
        self.assertEqual(item["image"]["provider"], "abs")

    def test_track_falls_back_to_metadata_image(self):
        item = ma.simplify(TRACK)
        self.assertEqual(item["subtitle"], "The Band")
        self.assertEqual(item["image"], {"path": "p.jpg", "provider": "fs"})

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
