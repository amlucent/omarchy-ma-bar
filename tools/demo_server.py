#!/usr/bin/env python3
"""A tiny fake Music Assistant + Audiobookshelf server with made-up data.

Used to take the README screenshots without exposing a real library, real
speaker names or real server addresses. It speaks just enough of the Music
Assistant API (POST /api) and the Audiobookshelf bookmark API for the plugin.

    python3 tools/demo_server.py [port]          # default 8099

Switch what is "playing" with  curl localhost:8099/__demo/mode/music|book|podcast
Point the plugin at it with MA_PLUGIN_URL / MA_PLUGIN_TOKEN (see the README's
Development section), or temporarily with the settings form.
"""

import json
import struct
import sys
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

NOW = int(time.time())
STATE = {"mode": "music"}


# ------------------------------------------------------------------ artwork
def cover_png(seed, size=160):
    """A soft two-colour gradient, deterministic per seed."""
    h = sum(ord(c) * (i + 3) for i, c in enumerate(seed))
    a = ((h * 53) % 150 + 60, (h * 97) % 150 + 60, (h * 31) % 150 + 90)
    b = ((h * 71) % 120 + 20, (h * 13) % 120 + 20, (h * 89) % 120 + 40)
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            t = (x + y) / (2.0 * size)
            row += bytes(int(a[k] * (1 - t) + b[k] * t) for k in range(3))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def img(seed):
    return {"proxy_id": seed, "provider": "demo"}


# --------------------------------------------------------------------- data
def track(n, name, artist, album, secs):
    return {"media_type": "track", "uri": "library://track/%d" % n, "name": name, "duration": secs,
            "artists": [{"name": artist}], "album": {"name": album}, "image": img(album)}


TRACKS = [
    track(1, "Paper Lanterns", "The Example Band", "Paper Lanterns", 214),
    track(2, "Midnight Drive", "The Example Band", "Midnight Drive", 247),
    track(3, "Slow Orbit", "Sample Sisters", "Slow Orbit", 198),
    track(4, "Golden Hour", "Northern Lights Quartet", "Golden Hour", 262),
    track(5, "Lowlands", "Sample Sisters", "Slow Orbit", 231),
]
ALBUMS = [
    {"media_type": "album", "uri": "library://album/%d" % i, "name": n, "artists": [{"name": a}], "image": img(n)}
    for i, (n, a) in enumerate([("Paper Lanterns", "The Example Band"), ("Midnight Drive", "The Example Band"),
                                 ("Slow Orbit", "Sample Sisters"), ("Golden Hour", "Northern Lights Quartet"),
                                 ("Quiet Harbour", "Demo Ensemble"), ("Field Notes", "Sample Sisters")], 1)
]
PLAYLISTS = [
    {"media_type": "playlist", "uri": "library://playlist/%d" % i, "name": n, "image": img(n)}
    for i, n in enumerate(["Morning Focus", "Road Trip Mix", "Sunday Kitchen", "Evening Wind-down"], 1)
]


def chapters(n, total):
    step = total / n
    names = ["The Storm", "Keeper's Log", "Salt and Iron", "A Visitor", "The Long Night", "Low Tide",
             "Letters", "The Lamp", "Fog", "What the Sea Kept", "Morning Watch", "Homecoming"]
    return [{"position": i, "name": names[i % len(names)], "start": i * step, "end": (i + 1) * step}
            for i in range(n)]


BOOK_SECS = 9 * 3600 + 20 * 60
BOOKS = [
    {"media_type": "audiobook", "uri": "library://audiobook/%d" % i, "name": n, "authors": [{"name": a}],
     "duration": BOOK_SECS - i * 1500, "image": img(n),
     "provider_mappings": [{"provider_domain": "audiobookshelf", "item_id": "demo-book-%d" % i}],
     "metadata": {"chapters": chapters(12, BOOK_SECS - i * 1500)}, "fully_played": False,
     "resume_position_ms": (4 * 3600 + 32 * 60) * 1000 if i == 2 else None}
    for i, (n, a) in enumerate([("The Lighthouse Keeper", "A. Writer"), ("Letters from the Orchard", "J. Sample"),
                                 ("A Short History of Rain", "M. Example")], 1)
]


def podcast(i, name, publisher):
    return {"media_type": "podcast", "uri": "library://podcast/%d" % i, "name": name,
            "publisher": publisher, "image": img(name)}


PODCASTS = [podcast(i, n, p) for i, (n, p) in enumerate([
    ("The Daily Example", "Demo Radio"), ("Science Hour", "Sample Media"), ("Cooking Notes", "Test Kitchen"),
    ("Tech Weekly", "Example Networks"), ("History in Ten", "Demo Radio")], 1)]

EP_TITLES = {
    1: ["What the new budget means", "A quiet revolution in housing", "Why the bridge closed"],
    2: ["How bees find their way home", "The physics of a perfect bounce", "Inside the cold lab"],
    3: ["Bread without kneading", "Three sauces, one pan", "Ten-minute dinners"],
    4: ["The year of the tiny computer", "Why batteries got better", "Your phone, ten years on"],
    5: ["The lamp that changed shipping", "A map nobody trusted", "Letters across an ocean"],
}


def episodes_of(i):
    out = []
    for n, title in enumerate(EP_TITLES[i]):
        age_h = [3 + i * 2, 30 + i * 3, 80 + i * 4][n]
        out.append({"media_type": "podcast_episode", "uri": "library://podcast_episode/%d%d" % (i, n),
                    "name": title, "duration": (24 + 6 * n + i) * 60, "podcast": {"name": PODCASTS[i - 1]["name"]},
                    "image": img(PODCASTS[i - 1]["name"]), "position": (NOW - age_h * 3600) * 1000,
                    "fully_played": True if (i, n) == (3, 1) else None,
                    "resume_position_ms": 14 * 60 * 1000 if (i, n) in ((1, 1), (2, 0)) else None})
    return out


ALL_EPISODES = [e for i in range(1, 6) for e in episodes_of(i)]


def find(uri):
    for coll in (TRACKS, ALBUMS, PLAYLISTS, BOOKS, PODCASTS, ALL_EPISODES):
        for item in coll:
            if item["uri"] == uri:
                return item
    return None


# ------------------------------------------------------------------ players
def player(pid, name, provider, **kw):
    base = {"player_id": pid, "display_name": name, "provider": provider, "type": "player", "available": True,
            "playback_state": "idle", "volume_level": 30, "volume_muted": False, "group_childs": [],
            "synced_to": None, "active_group": None, "powered": True, "static_group_members": [],
            "supported_features": ["set_members", "volume_set", "volume_mute", "pause", "seek", "next_previous"]}
    base.update(kw)
    return base


PLAYERS = {p["player_id"]: p for p in [
    player("p_studio", "Studio Speaker", "sonos--demo", playback_state="playing", volume_level=35),
    player("p_kitchen", "Kitchen", "sonos--demo", volume_level=28),
    player("p_living", "Living Room", "sonos--demo", volume_level=42),
    player("p_bedroom", "Bedroom", "sonos--demo", volume_level=22),
    player("p_office", "Office Display", "airplay--demo", volume_level=30),
    player("p_group", "Downstairs", "sync_group", type="group", group_childs=["p_kitchen", "p_living"],
           playback_state="idle", supported_features=["set_members", "volume_set", "volume_mute", "pause"]),
]}
for pid, p in PLAYERS.items():
    if p["type"] == "player":
        p["can_group_with"] = [q for q in PLAYERS if q != pid and PLAYERS[q]["type"] == "player"]
PLAYERS["p_group"]["can_group_with"] = [q for q in PLAYERS if PLAYERS[q]["type"] == "player"]


def set_members(args):
    target = PLAYERS.get(args.get("target_player"))
    if not target:
        return None
    members = set(target["group_childs"])
    for pid in args.get("player_ids_to_add") or []:
        members.add(pid)
        if PLAYERS[pid]["type"] == "player" and target["type"] == "player":
            PLAYERS[pid]["synced_to"] = target["player_id"]
    for pid in args.get("player_ids_to_remove") or []:
        members.discard(pid)
        PLAYERS[pid]["synced_to"] = None
    if target["type"] == "player":
        members.add(target["player_id"]) if len(members) > 0 else None
        if members == {target["player_id"]}:
            members = set()
    target["group_childs"] = sorted(members)
    return None


# -------------------------------------------------------------------- queue
def now_item():
    mode = STATE["mode"]
    if mode == "book":
        b = BOOKS[0]
        return b, (3 * 3600 + 5 * 60 + 40), 1.0
    if mode == "podcast":
        e = ALL_EPISODES[0]
        return e, 9 * 60 + 12, 1.25
    return TRACKS[1], 96, 1.0


def queue_get():
    item, elapsed, speed = now_item()
    return {"queue_id": "p_studio", "state": "playing", "elapsed_time": elapsed, "playback_speed": speed,
            "shuffle_enabled": False, "repeat_mode": "off", "current_index": 1,
            "current_item": {"name": item["name"], "duration": item["duration"], "media_item": item,
                             "image": item.get("image")}}


def queue_items():
    items = TRACKS if STATE["mode"] == "music" else [now_item()[0]] + TRACKS[2:4]
    return [{"queue_item_id": "q%d" % i, "name": t["name"], "duration": t["duration"], "media_item": t,
             "image": t.get("image"), "available": True} for i, t in enumerate(items)]


def search(args):
    q = (args.get("search_query") or "").lower()
    pick = lambda coll: [i for i in coll if q in i["name"].lower()][:6]
    return {"tracks": pick(TRACKS), "albums": pick(ALBUMS), "playlists": pick(PLAYLISTS),
            "audiobooks": pick(BOOKS), "podcasts": pick(PODCASTS)}


def lib(coll):
    return lambda args: [i for i in coll if (args.get("search") or "").lower() in i["name"].lower()]


def ep_by_id(args):
    uri = "library://podcast_episode/" + args["item_id"]
    return find(uri)


COMMANDS = {
    "players/all": lambda a: list(PLAYERS.values()),
    "players/get": lambda a: PLAYERS.get(a.get("player_id")),
    "players/cmd/set_members": set_members,
    "players/cmd/volume_set": lambda a: PLAYERS[a["player_id"]].update(volume_level=a["volume_level"]),
    "players/cmd/volume_mute": lambda a: PLAYERS[a["player_id"]].update(volume_muted=a["muted"]),
    "player_queues/get": lambda a: queue_get(),
    "player_queues/items": lambda a: queue_items(),
    "providers": lambda a: [{"domain": "universal_group", "available": True}],
    "music/in_progress_items": lambda a: [BOOKS[1], ALL_EPISODES[1], ALL_EPISODES[3]],
    "music/recently_played_items": lambda a: [TRACKS[1], ALBUMS[2], PLAYLISTS[0], TRACKS[3]],
    "music/albums/library_items": lib(ALBUMS),
    "music/playlists/library_items": lib(PLAYLISTS),
    "music/tracks/library_items": lib(TRACKS),
    "music/artists/library_items": lambda a: [],
    "music/audiobooks/library_items": lib(BOOKS),
    "music/podcasts/library_items": lib(PODCASTS),
    "music/audiobooks/get": lambda a: find("library://audiobook/" + a["item_id"]),
    "music/podcasts/podcast_episode": ep_by_id,
    "music/podcasts/podcast_episodes": lambda a: episodes_of(int(a["item_id"])),
    "music/search": search,
}

BOOKMARKS = [{"libraryItemId": "demo-book-1", "time": 4360, "title": "Great line about the lamp",
              "createdAt": NOW * 1000},
             {"libraryItemId": "demo-book-1", "time": 11110, "title": "Come back to this", "createdAt": NOW * 1000}]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/imageproxy/"):
            return self._send(200, cover_png(unquote(path.rsplit("/", 1)[1])), "image/png")
        if path.startswith("/__demo/mode/"):
            STATE["mode"] = path.rsplit("/", 1)[1]
            return self._send(200, {"mode": STATE["mode"]})
        if path == "/api/me":
            return self._send(200, {"bookmarks": BOOKMARKS})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        path = urlsplit(self.path).path
        if path == "/api":
            fn = COMMANDS.get(body.get("command"))
            result = fn(body.get("args") or {}) if fn else None
            return self._send(200, result)
        if path.startswith("/api/me/item/") and path.endswith("/bookmark"):
            BOOKMARKS.append({"libraryItemId": path.split("/")[4], "time": body.get("time", 0),
                              "title": body.get("title", ""), "createdAt": int(time.time() * 1000)})
            return self._send(200, {})
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        self._send(200, b"OK", "text/plain")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print("demo server on http://127.0.0.1:%d" % port)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
