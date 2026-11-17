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
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

KEYRING_SERVICE = "omarchy-music-assistant"
KEYRING_ACCOUNT = "api-token"
ABS_KEYRING_ACCOUNT = "abs-api-token"
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


class NoPlayerError(MaError):
    """No player is chosen and this computer has none of its own."""


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

def store_token(token, account=KEYRING_ACCOUNT, label="Music Assistant plugin token"):
    """Pipe the token into secret-tool on stdin (never argv, so it cannot show
    up in the process list)."""
    token = (token or "").strip()
    if not token:
        raise MaError("no token received")
    try:
        proc = subprocess.run(
            ["secret-tool", "store", "--label=" + label,
             "service", KEYRING_SERVICE, "account", account],
            input=token, text=True, capture_output=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MaError("could not reach the system keyring (is gnome-keyring running?)")
    if proc.returncode != 0:
        raise MaError("could not save the token to the system keyring")


def load_token(account=KEYRING_ACCOUNT, env_name="MA_PLUGIN_TOKEN"):
    env = os.environ.get(env_name)  # dev/test override; never written anywhere
    if env:
        return env.strip()
    try:
        proc = subprocess.run(
            ["secret-tool", "lookup", "service", KEYRING_SERVICE, "account", account],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def clear_token(account=KEYRING_ACCOUNT):
    try:
        subprocess.run(
            ["secret-tool", "clear", "service", KEYRING_SERVICE, "account", account],
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
            if command.startswith("player_queues/"):
                raise MaError("the server could not play that (a provider such as Spotify may be failing; "
                              "check the Music Assistant log)")
            raise MaError("the server could not find or load that item (see the Music Assistant log)")
        raise MaError("the server returned an error (HTTP %d)" % err.code)
    except (URLError, OSError):
        raise MaError("could not reach the Music Assistant server")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise MaError("the server sent a response I could not read")


def abs_call(base_url, token, method, path, body=None, opener=urlopen):
    """One request to an Audiobookshelf server; returns decoded JSON (or None)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": "Bearer " + token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(base_url + path, data=data, method=method, headers=headers)
    try:
        with opener(req, timeout=TIMEOUT_S) as resp:
            raw = read_limited(resp)
    except HTTPError as err:
        if err.code in (401, 403):
            raise MaError("Audiobookshelf rejected the token")
        if err.code == 404:
            raise MaError("Audiobookshelf could not find that item")
        raise MaError("Audiobookshelf returned an error (HTTP %d)" % err.code)
    except (URLError, OSError):
        raise MaError("could not reach the Audiobookshelf server")
    if not raw.strip():
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        # Audiobookshelf answers some writes (a delete) with plain text such as
        # "OK"; only a read has to be JSON.
        if method != "GET":
            return None
        raise MaError("Audiobookshelf sent a response I could not read")


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
    # For a podcast episode, MA keeps its publish time (ms since the epoch) in `position`.
    pos = item.get("position")
    published = int(pos / 1000) if mtype == "podcast_episode" and isinstance(pos, (int, float)) and pos > 10 ** 11 else None
    return {
        "published": published,
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
        "isGroup": player.get("type") == "group",
        "members": list(player.get("group_childs") or player.get("static_group_members") or []),
    }


def _leader_id(player):
    """Playback for a grouped player runs on its leader's queue."""
    return player.get("active_group") or player.get("synced_to") or player.get("player_id", "")


def _is_group(player):
    return player.get("type") == "group"


def _can_group(player):
    return "set_members" in (player.get("supported_features") or [])


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
            "uri": media.get("uri", ""),
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


def leader_of(base_url, token, player_id, call=api_call):
    """(leader id, leader player or None) for a player that may be in a group."""
    try:
        player = call(base_url, token, "players/get", {"player_id": player_id})
    except MaError:
        return player_id, None
    if not isinstance(player, dict):
        return player_id, None
    lid = _leader_id(player) or player_id
    if lid == player_id:
        return lid, player
    try:
        leader = call(base_url, token, "players/get", {"player_id": lid})
    except MaError:
        leader = None
    return lid, leader if isinstance(leader, dict) else None


def now_for_player(base_url, token, player_id, full=False, call=api_call):
    """Like now_playing, but follows a grouped player to its leader's queue."""
    lid, leader = leader_of(base_url, token, player_id, call=call)
    out = now_playing(base_url, token, lid, call=call)
    out["id"] = player_id
    out["leader"] = lid
    # MA lists the leader among its own group members once a group exists.
    childs = list((leader or {}).get("group_childs") or [])
    out["groupSize"] = len(childs) if _is_group(leader or {}) else len(set([lid] + childs))
    if full:
        player = leader or {}
        out["volume"] = player.get("volume_level")
        out["muted"] = player.get("volume_muted")
        out["player"] = player.get("display_name") or player.get("name", "")
    return out


def speakers(base_url, token, player_id, call=api_call):
    """Everything the Speakers view needs: the group the selected player is in
    and every other player that could join it."""
    players = [p for p in call(base_url, token, "players/all") if isinstance(p, dict)]
    by_id = {p.get("player_id"): p for p in players}
    selected = by_id.get(player_id) or {}
    lid = _leader_id(selected) or player_id
    leader = by_id.get(lid) or selected
    members = set([lid] + list(leader.get("group_childs") or []))
    joinable = set(leader.get("can_group_with") or [])
    rows = []
    for p in players:
        pid = p.get("player_id")
        if not p.get("available") or _is_group(p):
            continue
        member = pid in members
        if not (member or (pid in joinable and _can_group(p))):
            continue
        rows.append({
            "id": pid,
            "name": p.get("display_name") or p.get("name", ""),
            "provider": p.get("provider", ""),
            "state": p.get("playback_state"),
            "volume": p.get("volume_level"),
            "muted": p.get("volume_muted"),
            "member": member,
            "leader": pid == lid,
        })
    rows.sort(key=lambda r: (not r["leader"], not r["member"], r["name"].lower()))
    try:
        mixed_ok = any(p.get("domain") == "universal_group" and p.get("available")
                       for p in call(base_url, token, "providers") if isinstance(p, dict))
    except MaError:
        mixed_ok = False
    return {"mixedGroups": mixed_ok, "leader": lid, "leaderName": leader.get("display_name") or leader.get("name", ""),
            "canGroup": _can_group(leader), "isGroup": _is_group(leader), "speakers": rows}


def group_members(base_url, token, player_id, add=(), remove=(), call=api_call):
    lid, _ = leader_of(base_url, token, player_id, call=call)
    add, remove = [a for a in add if a and a != lid], [r for r in remove if r]
    if lid in remove:
        raise MaError("the leader can't be removed; ungroup instead")
    if not add and not remove:
        return {"ok": True, "leader": lid}
    call(base_url, token, "players/cmd/set_members",
         {"target_player": lid, "player_ids_to_add": add or None, "player_ids_to_remove": remove or None})
    return {"ok": True, "leader": lid}


def ungroup(base_url, token, player_id, call=api_call):
    lid, leader = leader_of(base_url, token, player_id, call=call)
    children = [c for c in ((leader or {}).get("group_childs") or []) if c != lid]
    if children:
        call(base_url, token, "players/cmd/set_members",
             {"target_player": lid, "player_ids_to_add": None, "player_ids_to_remove": children})
    return {"ok": True, "leader": lid}


def set_player_volume(base_url, token, player_id, level, call=api_call):
    level = max(0, min(100, int(float(level))))
    call(base_url, token, "players/cmd/volume_set", {"player_id": player_id, "volume_level": level})
    return {"ok": True}


def set_player_mute(base_url, token, player_id, muted, call=api_call):
    call(base_url, token, "players/cmd/volume_mute", {"player_id": player_id, "muted": bool(muted)})
    return {"ok": True}


# ------------------------------------------------------- Music Assistant groups
# Saved groups are real Music Assistant group players (the Sync Group provider),
# so they show up in every Music Assistant client, not only in this plugin.

GROUP_PROVIDERS = ("sync_group", "universal_group")   # in sync / mixed brands (not in sync)


def save_group(base_url, token, selected_id, name, members, provider="sync_group", call=api_call):
    """Create a permanent group player from `members`. Members are the speakers
    currently ticked; any ad-hoc group they form is dissolved first so the two
    don't fight over the same speakers."""
    name = (name or "").strip()[:40]
    members = [m for m in dict.fromkeys(members) if m]
    if not name:
        raise MaError("give the group a name")
    if len(members) < 2:
        raise MaError("a group needs at least two speakers")
    if provider not in GROUP_PROVIDERS:
        raise MaError("unknown group type")
    lid, leader = leader_of(base_url, token, selected_id, call=call) if selected_id else ("", None)
    if lid in members and (leader or {}).get("type") != "group":
        ungroup(base_url, token, selected_id, call=call)
    made = call(base_url, token, "players/create_group_player",
                {"provider": provider, "name": name, "members": members, "dynamic": True})
    return {"ok": True, "id": (made or {}).get("player_id", "")}


def delete_group(base_url, token, player_id, call=api_call):
    """Remove a group player. Refuses anything that isn't a group, so a real
    speaker can never be deleted from here."""
    try:
        player = call(base_url, token, "players/get", {"player_id": player_id})
    except MaError:
        player = None
    if not isinstance(player, dict) or not _is_group(player):
        raise MaError("that isn't a group")
    call(base_url, token, "players/remove_group_player", {"player_id": player_id})
    return {"ok": True}


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
        if player["provider"] in ("sendspin", "universal_player") and player["available"] \
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
    return {"continue": with_resume(base_url, token, in_progress(base_url, token, limit, call=call), call=call),
            "recent": recently_played(base_url, token, limit, call=call)}


def podcasts_home(base_url, token, recent=25, continue_limit=20, call=api_call):
    """The Podcasts tab: unfinished episodes, the newest episodes across every
    show, then the shows themselves."""
    shows = [s for s in call(base_url, token, "music/podcasts/library_items", {"limit": 500}) or []
             if isinstance(s, dict)]

    def of_show(show):
        provider, item_id = split_uri(show.get("uri", ""))
        if not provider:
            return []
        try:
            return call(base_url, token, "music/podcasts/podcast_episodes",
                        {"item_id": item_id, "provider_instance_id_or_domain": provider}) or []
        except MaError:
            return []   # one broken feed shouldn't empty the tab

    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(of_show, shows))
    seen, newest = set(), []
    for batch in batches:
        for raw in batch:
            item = simplify(raw, base_url)
            if item and item["uri"] not in seen:
                seen.add(item["uri"])
                newest.append(item)
    newest.sort(key=lambda i: i["published"] or 0, reverse=True)
    unfinished = [i for i in in_progress(base_url, token, continue_limit, call=call) if i["type"] == "podcast_episode"]
    unfinished_uris = set(i["uri"] for i in unfinished)
    # The in-progress records are slim (no show name or date); fill them from the episode lists.
    known = dict((i["uri"], i) for i in newest)
    for item in unfinished:
        full = known.get(item["uri"])
        if full:
            item["subtitle"] = item["subtitle"] or full["subtitle"]
            item["published"] = item["published"] or full["published"]
    return {
        "continue": with_resume(base_url, token, unfinished, call=call),
        "newest": [i for i in newest if i["uri"] not in unfinished_uris][:recent],
        "shows": [i for i in (simplify(x, base_url) for x in shows) if i],
    }


MARKABLE = ("audiobook", "podcast_episode")


def abs_progress_path(item):
    """'/api/me/progress/<book>[/<episode>]' for an Audiobookshelf-backed item, else ''."""
    raw = abs_item_id(item)
    if not raw:
        return ""
    book, _sp, episode = raw.partition(" ")
    path = "/api/me/progress/" + quote(book, safe="")
    return path + "/" + quote(episode, safe="") if episode else path


def mark_played(base_url, token, item_type, uri, played, call=api_call, abs_ctx=None, abs_request=abs_call):
    """Mark a book or episode finished, or reset it to unplayed.

    Music Assistant forwards "played" to Audiobookshelf but not "unplayed", and
    it reads the played state back from Audiobookshelf, so resetting an
    Audiobookshelf item has to be done there as well."""
    if item_type not in MARKABLE:
        raise MaError("only audiobooks and podcast episodes can be marked")
    item = get_detail(base_url, token, item_type, uri, call=call)
    if played:
        call(base_url, token, "music/mark_played",
             {"media_item": item, "fully_played": True, "seconds_played": int(item.get("duration") or 0),
              "is_playing": False, "user_initiated": True})
        return {"ok": True}
    call(base_url, token, "music/mark_unplayed", {"media_item": item})
    path = abs_progress_path(item)
    if path:
        abs_url, abs_token = abs_ctx()   # raises a friendly error when not connected
        abs_request(abs_url, abs_token, "PATCH", path,
                    {"isFinished": False, "progress": 0, "currentTime": 0})
    return {"ok": True}


def split_uri(uri):
    """'library://podcast/12' -> ('library', '12'); anything else -> ('', '').
    Provider instance ids are case-sensitive, so the string is cut by hand
    rather than with urlsplit (which lower-cases the scheme)."""
    scheme, sep, rest = (uri or "").partition("://")
    _kind, slash, item_id = rest.partition("/")
    if scheme and sep and slash and item_id:
        return scheme, item_id
    return "", ""


def episodes(base_url, token, uri, call=api_call):
    provider, item_id = split_uri(uri)
    if not provider:
        raise MaError("that is not a podcast address")
    items = call(base_url, token, "music/podcasts/podcast_episodes",
                 {"item_id": item_id, "provider_instance_id_or_domain": provider})
    return [i for i in (simplify(x, base_url) for x in items or []) if i]


# -------------------------------------------------------------------- control

def queue_items(base_url, token, queue_id, limit=200, call=api_call):
    """The play queue, in order, with the current item marked."""
    queue = call(base_url, token, "player_queues/get", {"queue_id": queue_id})
    items = call(base_url, token, "player_queues/items", {"queue_id": queue_id, "limit": limit, "offset": 0})
    current = queue.get("current_index") if isinstance(queue, dict) else None
    out = []
    for i, qi in enumerate(items if isinstance(items, list) else []):
        media = qi.get("media_item") or {}
        # The item's own "index" field is not its queue position; use the
        # position in the list the server returned.
        out.append({
            "id": qi.get("queue_item_id", ""),
            "index": i,
            "name": qi.get("name") or media.get("name", ""),
            "subtitle": (_names(media.get("artists")) or _names(media.get("authors"))
                         or (media.get("podcast") or {}).get("name", "")),
            "type": media.get("media_type", ""),
            "duration": qi.get("duration"),
            "cover": cover_url(base_url, image_ref(qi) or image_ref(media)),
            "current": current is not None and i == current,
            "available": qi.get("available", True),
        })
    return out


ENQUEUE_OPTIONS = {"play": "play", "next": "next", "add": "add", "replace": "replace"}


def enqueue(base_url, token, queue_id, mode, uri, call=api_call):
    """Put something on the queue: 'next' plays it after the current item,
    'add' puts it at the end, 'play' starts it now, 'replace' empties the queue
    first."""
    option = ENQUEUE_OPTIONS.get(mode)
    if not option:
        raise MaError("unknown queue action: %s" % mode)
    if not uri:
        raise MaError("nothing to add")
    call(base_url, token, "player_queues/play_media", {"queue_id": queue_id, "media": uri, "option": option})
    return {"ok": True}


def queue_remove(base_url, token, queue_id, item_id, call=api_call):
    if not item_id:
        raise MaError("no queue item given")
    call(base_url, token, "player_queues/delete_item", {"queue_id": queue_id, "item_id_or_index": item_id})
    return {"ok": True}


def queue_move(base_url, token, queue_id, item_id, shift, call=api_call):
    """Move an item up (negative) or down (positive) the queue by `shift`."""
    if not item_id:
        raise MaError("no queue item given")
    shift = int(shift)
    if shift == 0:
        return {"ok": True}
    call(base_url, token, "player_queues/move_item",
         {"queue_id": queue_id, "queue_item_id": item_id, "pos_shift": shift})
    return {"ok": True}


def queue_jump(base_url, token, queue_id, target, call=api_call):
    """Play a queue item, given its position (a number) or its queue item id."""
    target = str(target)
    if not target:
        raise MaError("no queue item given")
    call(base_url, token, "player_queues/play_index",
         {"queue_id": queue_id, "index": int(target) if target.isdigit() else target})
    return {"ok": True}


def queue_clear(base_url, token, queue_id, call=api_call):
    call(base_url, token, "player_queues/clear", {"queue_id": queue_id})
    return {"ok": True}


DETAIL_COMMANDS = {
    "audiobook": "music/audiobooks/get",
    "podcast_episode": "music/podcasts/podcast_episode",
}


def get_detail(base_url, token, item_type, uri, call=api_call):
    """The full item (the lists only carry slim records without resume data)."""
    command = DETAIL_COMMANDS.get(item_type)
    provider, item_id = split_uri(uri)
    if not command or not provider:
        raise MaError("no details for that kind of item")
    item = call(base_url, token, command, {"item_id": item_id, "provider_instance_id_or_domain": provider})
    if not isinstance(item, dict):
        raise MaError("the server sent no details")
    return item


def clean_chapters(item):
    out = []
    for c in ((item.get("metadata") or {}).get("chapters") or []):
        if isinstance(c, dict) and c.get("start") is not None:
            out.append({"position": c.get("position", len(out) + 1), "name": c.get("name") or "",
                        "start": float(c["start"]), "end": c.get("end")})
    return out


def details(base_url, token, item_type, uri, call=api_call):
    """Chapters, resume position and the Audiobookshelf id for one item."""
    item = get_detail(base_url, token, item_type, uri, call=call)
    resume_ms = item.get("resume_position_ms")
    return {
        "duration": item.get("duration"),
        "resume": (resume_ms / 1000.0) if isinstance(resume_ms, (int, float)) else None,
        "played": bool(item.get("fully_played")),
        "chapters": clean_chapters(item),
        "abs_id": abs_item_id(item),
    }


def abs_item_id(item):
    for m in (item or {}).get("provider_mappings") or []:
        if m.get("provider_domain") == "audiobookshelf" and m.get("item_id"):
            return m["item_id"]
    return ""


def with_resume(base_url, token, items, call=api_call):
    """Fill in resume position and exact duration for in-progress rows."""
    def fill(item):
        if item["type"] not in DETAIL_COMMANDS:
            return item
        try:
            full = get_detail(base_url, token, item["type"], item["uri"], call=call)
        except Exception:  # best effort: a failed lookup must not break the Home feed
            return item
        resume_ms = full.get("resume_position_ms")
        if isinstance(resume_ms, (int, float)):
            item["resume"] = resume_ms / 1000.0
        if full.get("duration"):
            item["duration"] = full["duration"]
        if item["type"] == "audiobook" and not item["subtitle"]:
            item["subtitle"] = _names(full.get("authors"))
        return item
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(fill, items))


def abs_bookmarks(abs_url, abs_token, abs_id, opener=urlopen):
    me = abs_call(abs_url, abs_token, "GET", "/api/me", opener=opener)
    marks = [b for b in (me or {}).get("bookmarks") or [] if b.get("libraryItemId") == abs_id]
    marks.sort(key=lambda b: b.get("time", 0))
    return [{"time": b.get("time", 0), "title": b.get("title", ""), "created": b.get("createdAt")}
            for b in marks]


def fmt_time(seconds):
    seconds = int(max(0, seconds))
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def abs_add_bookmark(abs_url, abs_token, abs_id, seconds, title="", opener=urlopen):
    seconds = int(float(seconds))
    title = (title or "").strip() or "Bookmark @ " + fmt_time(seconds)
    abs_call(abs_url, abs_token, "POST", "/api/me/item/%s/bookmark" % quote(abs_id, safe=""),
             {"time": seconds, "title": title}, opener=opener)
    return {"ok": True, "time": seconds, "title": title}


def abs_delete_bookmark(abs_url, abs_token, abs_id, seconds, opener=urlopen):
    abs_call(abs_url, abs_token, "DELETE",
             "/api/me/item/%s/bookmark/%d" % (quote(abs_id, safe=""), int(float(seconds))), opener=opener)
    return {"ok": True}


def current_media_uri(base_url, token, queue_id, call=api_call):
    queue = call(base_url, token, "player_queues/get", {"queue_id": queue_id})
    current = (queue or {}).get("current_item") or {}
    return (current.get("media_item") or {}).get("uri", "")


def go_to(base_url, token, queue_id, uri, seconds, call=api_call, sleep=time.sleep, tries=24):
    """Play `uri` (unless it is already playing) and seek to `seconds`."""
    if current_media_uri(base_url, token, queue_id, call=call) != uri:
        call(base_url, token, "player_queues/play_media", {"queue_id": queue_id, "media": uri})
        for _ in range(tries):
            sleep(0.5)
            if current_media_uri(base_url, token, queue_id, call=call) == uri:
                break
        else:
            raise MaError("the item did not start in time")
    call(base_url, token, "player_queues/seek", {"queue_id": queue_id, "position": int(float(seconds))})
    return {"ok": True}


CONTROLS = {
    "playpause": ("player_queues/play_pause", lambda a: {}),
    "next": ("player_queues/next", lambda a: {}),
    "previous": ("player_queues/previous", lambda a: {}),
    "pause": ("player_queues/pause", lambda a: {}),
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


def _abs_ctx():
    config = load_config()
    token = load_token(ABS_KEYRING_ACCOUNT, "MA_PLUGIN_ABS_TOKEN")
    url = os.environ.get("MA_PLUGIN_ABS_URL") or config.get("absUrl", "")
    if not url or not token:
        raise MaError("connect Audiobookshelf in the settings to use bookmarks")
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
            clear_token(ABS_KEYRING_ACCOUNT)
            try:
                os.remove(CONFIG_PATH)
            except OSError:
                pass
            _emit({"ok": True})
        if cmd == "abs-status":
            config = load_config()
            token = load_token(ABS_KEYRING_ACCOUNT, "MA_PLUGIN_ABS_TOKEN")
            url = os.environ.get("MA_PLUGIN_ABS_URL") or config.get("absUrl", "")
            _emit({"configured": bool(url and token), "absUrl": url})
        if cmd == "abs-setup":
            base = normalize_base_url(rest[0] if rest else "")
            token = sys.stdin.readline().strip()
            if not token:
                raise MaError("no token received")
            abs_call(base, token, "GET", "/api/me")  # verify before saving anything
            store_token(token, ABS_KEYRING_ACCOUNT, "Audiobookshelf token (Music Assistant plugin)")
            config = load_config()
            config["absUrl"] = base
            save_config(config)
            _emit({"ok": True, "absUrl": base})
        if cmd == "abs-disconnect":
            clear_token(ABS_KEYRING_ACCOUNT)
            config = load_config()
            config.pop("absUrl", None)
            save_config(config)
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
        if cmd in ("now", "play", "ctl", "queue", "enqueue", "queue-remove", "queue-move", "queue-jump", "queue-clear", "bookmark-go") and not configured and not (cmd == "now" and rest):
            queue_id = resolve_queue(base, token)
            if not queue_id:
                raise NoPlayerError("no player selected and this computer has no Music Assistant player")

        if queue_id and cmd in ("play", "ctl", "queue", "enqueue", "queue-remove", "queue-move", "queue-jump", "queue-clear", "bookmark-go"):
            queue_id = leader_of(base, token, queue_id)[0]

        if cmd == "players":
            _emit(list_players(base, token))
        if cmd == "speakers":
            _emit(speakers(base, token, rest[0] if rest else queue_id))
        if cmd == "group":
            # group <player> add|remove <id>...
            if len(rest) < 3 or rest[1] not in ("add", "remove"):
                raise MaError("usage: group <player> add|remove <id>...")
            ids = rest[2:]
            _emit(group_members(base, token, rest[0], add=ids if rest[1] == "add" else (),
                                remove=ids if rest[1] == "remove" else ()))
        if cmd == "ungroup":
            _emit(ungroup(base, token, rest[0] if rest else queue_id))
        if cmd == "player-volume":
            _emit(set_player_volume(base, token, rest[0], rest[1]))
        if cmd == "player-mute":
            _emit(set_player_mute(base, token, rest[0], rest[1] in ("1", "true", "yes")))
        if cmd == "group-save":
            # group-save <sync_group|universal_group> <name> <player id>...
            # (also switches the panel to the new group)
            made = save_group(base, token, queue_id, rest[1] if len(rest) > 1 else "", rest[2:],
                              provider=rest[0] if rest else "")
            config = load_config()
            config["playerId"] = made["id"]
            config.pop("groupPresets", None)
            save_config(config)
            _emit(made)
        if cmd == "group-delete":
            result = delete_group(base, token, rest[0] if rest else "")
            config = load_config()
            if config.get("playerId") == (rest[0] if rest else ""):
                config["playerId"] = ""
                save_config(config)
            _emit(result)
        if cmd == "now":
            _emit(now_for_player(base, token, rest[0] if rest else queue_id, full=full))
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
        if cmd == "podcasts-home":
            _emit(podcasts_home(base, token))
        if cmd == "mark":
            # mark <audiobook|podcast_episode> <uri> <played|unplayed>
            if len(rest) < 3 or rest[2] not in ("played", "unplayed"):
                raise MaError("usage: mark <type> <uri> played|unplayed")
            _emit(mark_played(base, token, rest[0], rest[1], rest[2] == "played", abs_ctx=_abs_ctx))
        if cmd == "in-progress":
            _emit(in_progress(base, token))
        if cmd == "recent":
            _emit(recently_played(base, token))
        if cmd == "details":
            _emit(details(base, token, rest[0] if rest else "", rest[1] if len(rest) > 1 else ""))
        if cmd in ("bookmarks", "bookmark-add", "bookmark-delete", "bookmark-go"):
            uri = rest[0] if rest else ""
            abs_url, abs_token = _abs_ctx()
            abs_id = abs_item_id(get_detail(base, token, "audiobook", uri))
            if not abs_id:
                raise MaError("bookmarks are only available for Audiobookshelf audiobooks")
            if cmd == "bookmarks":
                _emit(abs_bookmarks(abs_url, abs_token, abs_id))
            if cmd == "bookmark-add":
                _emit(abs_add_bookmark(abs_url, abs_token, abs_id, rest[1], " ".join(rest[2:])))
            if cmd == "bookmark-delete":
                _emit(abs_delete_bookmark(abs_url, abs_token, abs_id, rest[1]))
            _emit(go_to(base, token, queue_id, uri, rest[1]))
        if cmd == "queue":
            _emit(queue_items(base, token, queue_id))
        if cmd == "enqueue":
            _emit(enqueue(base, token, queue_id, rest[0] if rest else "", rest[1] if len(rest) > 1 else ""))
        if cmd == "queue-remove":
            _emit(queue_remove(base, token, queue_id, rest[0] if rest else ""))
        if cmd == "queue-move":
            _emit(queue_move(base, token, queue_id, rest[0] if rest else "", rest[1] if len(rest) > 1 else 0))
        if cmd == "queue-jump":
            _emit(queue_jump(base, token, queue_id, rest[0] if rest else 0))
        if cmd == "queue-clear":
            _emit(queue_clear(base, token, queue_id))
        if cmd == "play":
            _emit(play(base, token, queue_id, rest[0] if rest else ""))
        if cmd == "ctl":
            if not rest:
                raise MaError("missing control action")
            _emit(control(base, token, queue_id, rest[0], rest[1:]))
        _emit({"error": "unknown command: %s" % cmd}, 1)
    except NoPlayerError as err:
        _emit({"error": str(err), "code": "no_player"}, 1)
    except MaError as err:
        _emit({"error": str(err)}, 1)
    except (IndexError, ValueError):
        _emit({"error": "bad arguments"}, 1)


if __name__ == "__main__":
    main(sys.argv)
