#!/usr/bin/env python3
"""Music Assistant backend for the Omarchy bar plugin.

Talks JSON over HTTP to a Music Assistant server (POST <server>/api with a
long-lived bearer token). Standard library only. Every command prints one JSON
document on stdout; failures print {"error": "..."} and exit 1.

Secrets: the token lives in the system keyring (secret-tool), never in a file
and never on a command line. Only the server URL is written to the config file.
"""
import json
import os
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

KEYRING_SERVICE = "omarchy-music-assistant"
KEYRING_ACCOUNT = "api-token"
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "omarchy-music-assistant",
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

MAX_BYTES = 32 * 1024 * 1024
TIMEOUT_S = 15
DEADLINE_S = 45
_CHUNK = 64 * 1024

# Media types the library/search commands know about, mapped to the API's
# library listing command.
LIBRARY_TYPES = {
    "album": "music/albums/library_items",
    "artist": "music/artists/library_items",
    "playlist": "music/playlists/library_items",
    "track": "music/tracks/library_items",
    "audiobook": "music/audiobooks/library_items",
    "podcast": "music/podcasts/library_items",
}


class MaError(Exception):
    """A failure the UI can show to the user as-is."""


# --------------------------------------------------------------------- config

def load_config(path=None):
    try:
        with open(path or CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config, path=None):
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def normalize_base_url(raw):
    """Accept host:port or a full URL; return scheme://host[:port] with no
    credentials, path, query or fragment."""
    raw = (raw or "").strip()
    if not raw:
        raise MaError("enter your Music Assistant server address")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise MaError("the server address must be http:// or https://")
    if parts.username or parts.password:
        raise MaError("do not put a username or password in the address")
    host = parts.hostname
    if ":" in host:  # IPv6 literal
        host = "[" + host + "]"
    port = ":" + str(parts.port) if parts.port else ""
    return "%s://%s%s" % (parts.scheme, host, port)


# -------------------------------------------------------------------- keyring

def store_token(token):
    """Pipe the token into secret-tool on stdin (never argv, so it cannot show
    up in the process list)."""
    token = (token or "").strip()
    if not token:
        raise MaError("no token received")
    try:
        proc = subprocess.run(
            ["secret-tool", "store", "--label=Music Assistant plugin token",
             "service", KEYRING_SERVICE, "account", KEYRING_ACCOUNT],
            input=token, text=True, capture_output=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MaError("could not reach the system keyring (is gnome-keyring running?)")
    if proc.returncode != 0:
        raise MaError("could not save the token to the system keyring")


def load_token():
    # Dev/test override; never written anywhere.
    env = os.environ.get("MA_PLUGIN_TOKEN")
    if env:
        return env.strip()
    try:
        proc = subprocess.run(
            ["secret-tool", "lookup", "service", KEYRING_SERVICE, "account", KEYRING_ACCOUNT],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def clear_token():
    try:
        subprocess.run(
            ["secret-tool", "clear", "service", KEYRING_SERVICE, "account", KEYRING_ACCOUNT],
            capture_output=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


# ------------------------------------------------------------------------ http

def read_limited(response, limit=MAX_BYTES, deadline_s=DEADLINE_S):
    """Read a response body, refusing anything over `limit` bytes or taking
    longer than `deadline_s` overall."""
    declared = (getattr(response, "headers", None) or {}).get("Content-Length")
    if declared and str(declared).isdigit() and int(declared) > limit:
        raise MaError("the server's response was too large")
    deadline = time.monotonic() + deadline_s
    chunks, total = [], 0
    while True:
        if time.monotonic() > deadline:
            raise MaError("the server took too long to answer")
        chunk = response.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise MaError("the server's response was too large")
        chunks.append(chunk)
    return b"".join(chunks)


def api_call(base_url, token, command, args=None, opener=urlopen):
    """POST one command to /api and return the decoded JSON result."""
    body = json.dumps({"message_id": "omarchy", "command": command, "args": args or {}})
    req = Request(
        base_url + "/api",
        data=body.encode("utf-8"),
        method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with opener(req, timeout=TIMEOUT_S) as resp:
            raw = read_limited(resp)
    except HTTPError as err:
        if err.code in (401, 403):
            raise MaError("the server rejected the token (create a new long-lived token)")
        if err.code >= 500:
            raise MaError("the server could not do that (a provider such as Spotify may be failing; "
                          "check the Music Assistant log)")
        raise MaError("the server returned an error (HTTP %d)" % err.code)
    except (URLError, OSError):
        raise MaError("could not reach the Music Assistant server")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise MaError("the server sent a response I could not read")


# ---------------------------------------------------------------- normalizing

def _names(items):
    return ", ".join(i.get("name", "") for i in (items or []) if isinstance(i, dict) and i.get("name"))


COVER_SIZES = (80, 160, 256, 512, 1024)


def image_ref(item):
    """Return the artwork's proxy id (and provider) for an item, or None.

    Raw artwork paths can be URLs that embed another service's token, so only
    the server's opaque proxy id is passed on; the UI fetches the picture from
    the Music Assistant server and never sees the original address."""
    candidates = []
    img = (item or {}).get("image")
    if isinstance(img, dict):
        candidates.append(img)
    candidates.extend(c for c in ((item or {}).get("metadata") or {}).get("images") or []
                      if isinstance(c, dict))
    for cand in candidates:
        if cand.get("proxy_id"):
            return {"proxy_id": cand["proxy_id"], "provider": cand.get("provider", "")}
    return None


def cover_url(base_url, ref, size=256):
    """Address of a cover on the server's image proxy (no auth needed)."""
    if not ref or not ref.get("proxy_id") or not base_url:
        return ""
    if size not in COVER_SIZES:
        size = 256
    return "%s/imageproxy/%s?size=%d&fmt=jpg" % (base_url, quote(ref["proxy_id"], safe=""), size)


def simplify(item, base_url=""):
    """Reduce an MA media item to the few fields the UI needs."""
    if not isinstance(item, dict):
        return None
    mtype = item.get("media_type", "")
    if mtype in ("album", "track", "podcast_episode"):
        subtitle = _names(item.get("artists")) or (item.get("podcast") or {}).get("name", "")
    elif mtype == "audiobook":
        subtitle = _names(item.get("authors"))
    elif mtype == "podcast":
        subtitle = item.get("publisher", "") or ""
    else:
        subtitle = ""
    resume_ms = item.get("resume_position_ms")
    return {
        "uri": item.get("uri", ""),
        "name": item.get("name", ""),
        "type": mtype,
        "subtitle": subtitle,
        "duration": item.get("duration"),
        "cover": cover_url(base_url, image_ref(item)),
        "played": bool(item.get("fully_played")),
        "resume": (resume_ms / 1000.0) if isinstance(resume_ms, (int, float)) else None,
    }


def simplify_player(player):
    return {
        "id": player.get("player_id", ""),
        "name": player.get("display_name") or player.get("name", ""),
        "provider": player.get("provider", ""),
        "available": bool(player.get("available")),
        "state": player.get("playback_state"),
        "volume": player.get("volume_level"),
        "muted": player.get("volume_muted"),
    }


def simplify_queue(queue, base_url=""):
    if not isinstance(queue, dict):
        return {"state": "idle", "item": None}
    current = queue.get("current_item") or {}
    media = current.get("media_item") or {}
    return {
        "id": queue.get("queue_id", ""),
        "state": queue.get("state", "idle"),
        "elapsed": queue.get("elapsed_time"),
        "speed": queue.get("playback_speed", 1.0),
        "shuffle": queue.get("shuffle_enabled"),
        "repeat": queue.get("repeat_mode"),
        "item": {
            "name": current.get("name") or media.get("name", ""),
            "duration": current.get("duration"),
            "type": media.get("media_type", ""),
            "subtitle": (_names(media.get("artists")) or _names(media.get("authors"))
                         or (media.get("podcast") or {}).get("name", "")),
            "cover": cover_url(base_url, image_ref(current) or image_ref(media)),
        } if current else None,
    }


# --------------------------------------------------------------------- queries

def list_players(base_url, token, call=api_call):
    players = call(base_url, token, "players/all")
    return [simplify_player(p) for p in players if isinstance(p, dict)]


def now_playing(base_url, token, queue_id, full=False, call=api_call):
    out = simplify_queue(call(base_url, token, "player_queues/get", {"queue_id": queue_id}), base_url)
    if full:
        # Volume lives on the player, not the queue.
        player = call(base_url, token, "players/get", {"player_id": queue_id})
        if isinstance(player, dict):
            out["volume"] = player.get("volume_level")
            out["muted"] = player.get("volume_muted")
            out["player"] = player.get("display_name") or player.get("name", "")
    return out


def search(base_url, token, query, limit=8, call=api_call):
    result = call(base_url, token, "music/search", {"search_query": query, "limit": limit})
    out = {}
    if isinstance(result, dict):
        for kind in ("tracks", "albums", "artists", "playlists", "audiobooks", "podcasts"):
            items = [simplify(i, base_url) for i in result.get(kind) or []]
            if items:
                out[kind] = [i for i in items if i]
    return out


def library(base_url, token, kind, limit=50, offset=0, query="", call=api_call):
    command = LIBRARY_TYPES.get(kind)
    if not command:
        raise MaError("unknown library type: %s" % kind)
    args = {"limit": limit, "offset": offset, "order_by": "sort_name"}
    if query:
        args["search"] = query
    return [i for i in (simplify(x, base_url) for x in call(base_url, token, command, args)) if i]


def resolve_queue(base_url, token, configured="", call=api_call):
    """The queue to control: the player picked in settings, otherwise this
    computer's own Sendspin player (matched by hostname), otherwise nothing."""
    if configured:
        return configured
    host = socket.gethostname().split(".")[0].lower()
    for player in list_players(base_url, token, call=call):
        if player["provider"] == "sendspin" and player["available"] \
                and player["name"].lower().split(".")[0] == host:
            return player["id"]
    return ""


def in_progress(base_url, token, limit=20, call=api_call):
    items = call(base_url, token, "music/in_progress_items", {"limit": limit})
    return [i for i in (simplify(x, base_url) for x in items or []) if i]


def recently_played(base_url, token, limit=20, call=api_call):
    items = call(base_url, token, "music/recently_played_items", {"limit": limit})
    return [i for i in (simplify(x, base_url) for x in items or []) if i]


def home(base_url, token, limit=12, call=api_call):
    """What the dropdown's Home tab shows: things to continue, then recents."""
    return {"continue": in_progress(base_url, token, limit, call=call),
            "recent": recently_played(base_url, token, limit, call=call)}


def split_uri(uri):
    """'library://podcast/12' -> ('library', '12'); anything else -> ('', '')."""
    parts = urlsplit(uri or "")
    item_id = parts.path.lstrip("/")
    if parts.scheme and parts.netloc and item_id:
        return parts.scheme, item_id
    return "", ""


def episodes(base_url, token, uri, call=api_call):
    provider, item_id = split_uri(uri)
    if not provider:
        raise MaError("that is not a podcast address")
    items = call(base_url, token, "music/podcasts/podcast_episodes",
                 {"item_id": item_id, "provider_instance_id_or_domain": provider})
    return [i for i in (simplify(x, base_url) for x in items or []) if i]


# -------------------------------------------------------------------- control

CONTROLS = {
    "playpause": ("player_queues/play_pause", lambda a: {}),
    "next": ("player_queues/next", lambda a: {}),
    "previous": ("player_queues/previous", lambda a: {}),
    "stop": ("player_queues/stop", lambda a: {}),
    "seek": ("player_queues/seek", lambda a: {"position": int(float(a[0]))}),
    "speed": ("player_queues/set_playback_speed", lambda a: {"speed": float(a[0])}),
}


def control(base_url, token, queue_id, action, extra=(), call=api_call):
    if action == "volume":
        level = max(0, min(100, int(float(extra[0]))))
        call(base_url, token, "players/cmd/volume_set", {"player_id": queue_id, "volume_level": level})
        return {"ok": True}
    spec = CONTROLS.get(action)
    if not spec:
        raise MaError("unknown control: %s" % action)
    command, build = spec
    try:
        args = build(list(extra))
    except (IndexError, ValueError):
        raise MaError("bad value for %s" % action)
    args["queue_id"] = queue_id
    call(base_url, token, command, args)
    return {"ok": True}


def play(base_url, token, queue_id, uri, call=api_call):
    if not uri:
        raise MaError("nothing to play")
    call(base_url, token, "player_queues/play_media", {"queue_id": queue_id, "media": uri})
    return {"ok": True}


# ------------------------------------------------------------------------ main

def _emit(obj, code=0):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.write("\n")
    sys.exit(code)


def _need_setup():
    config = load_config()
    token = load_token()
    url = os.environ.get("MA_PLUGIN_URL") or config.get("serverUrl")  # env: dev/test only
    if not url or not token:
        raise MaError("not connected yet")
    return url, token


def main(argv):
    if len(argv) < 2:
        _emit({"error": "usage: ma_backend.py <command> [args]"}, 1)
    cmd, rest = argv[1], argv[2:]
    try:
        if cmd == "status":
            config, token = load_config(), load_token()
            _emit({"configured": bool(config.get("serverUrl") and token),
                   "serverUrl": config.get("serverUrl", ""),
                   "player": config.get("playerId", "")})
        if cmd == "setup":
            # The token arrives on stdin so it is never visible in argv.
            base = normalize_base_url(rest[0] if rest else "")
            token = sys.stdin.readline().strip()
            api_call(base, token, "players/all")  # verify before saving anything
            store_token(token)
            config = load_config()
            config["serverUrl"] = base
            save_config(config)
            _emit({"ok": True, "serverUrl": base})
        if cmd == "disconnect":
            clear_token()
            try:
                os.remove(CONFIG_PATH)
            except OSError:
                pass
            _emit({"ok": True})
        if cmd == "set-player":
            config = load_config()
            config["playerId"] = rest[0] if rest else ""
            save_config(config)
            _emit({"ok": True})

        full = "--full" in rest
        rest = [a for a in rest if a != "--full"]
        base, token = _need_setup()
        configured = os.environ.get("MA_PLUGIN_QUEUE") or load_config().get("playerId", "")
        queue_id = configured
        if cmd in ("now", "play", "ctl") and not configured and not (cmd == "now" and rest):
            queue_id = resolve_queue(base, token)
            if not queue_id:
                raise MaError("no player selected and this computer has no Music Assistant player")

        if cmd == "players":
            _emit(list_players(base, token))
        if cmd == "now":
            _emit(now_playing(base, token, rest[0] if rest else queue_id, full=full))
        if cmd == "search":
            _emit(search(base, token, " ".join(rest)))
        if cmd == "library":
            kind = rest[0] if rest else ""
            limit = int(rest[1]) if len(rest) > 1 else 50
            offset = int(rest[2]) if len(rest) > 2 else 0
            _emit(library(base, token, kind, limit, offset, " ".join(rest[3:])))
        if cmd == "home":
            _emit(home(base, token))
        if cmd == "episodes":
            _emit(episodes(base, token, rest[0] if rest else ""))
        if cmd == "in-progress":
            _emit(in_progress(base, token))
        if cmd == "recent":
            _emit(recently_played(base, token))
        if cmd == "play":
            _emit(play(base, token, queue_id, rest[0] if rest else ""))
        if cmd == "ctl":
            if not rest:
                raise MaError("missing control action")
            _emit(control(base, token, queue_id, rest[0], rest[1:]))
        _emit({"error": "unknown command: %s" % cmd}, 1)
    except MaError as err:
        _emit({"error": str(err)}, 1)
    except (IndexError, ValueError):
        _emit({"error": "bad arguments"}, 1)


if __name__ == "__main__":
    main(sys.argv)
