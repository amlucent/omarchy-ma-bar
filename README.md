# Music Assistant for Omarchy

Control a [Music Assistant](https://www.music-assistant.io/) server from the
Omarchy bar. Early development (v0.1): now-playing state and play/pause/skip
from the bar icon. A dropdown to browse and play music, audiobooks and podcasts
is planned.

*An independent client. Not affiliated with or endorsed by the Music Assistant
project or the Omarchy project.*

## What works today

- Bar icon showing whether your player is playing, with a tooltip of the current
  title and artist/author.
- Left click: play / pause. Middle click: next.
- The backend (`scripts/ma_backend.py`) already supports searching, listing
  albums, artists, playlists, tracks, audiobooks and podcasts, in-progress and
  recently played items, player selection, seek, volume and playback speed. The
  dropdown that uses them is the next milestone.

## Requirements

- A Music Assistant server (developed against 2.10) and a **long-lived access
  token** (Music Assistant web UI -> Settings -> Profile).
- `python3`, `secret-tool` and a running keyring that provides the Secret
  Service, such as gnome-keyring.
- To play audio on this computer, the
  [Music Assistant Companion](https://www.music-assistant.io/companion-app/)
  app, which registers this computer as a player. Without it you can still
  control any other player on your server.

## Install

```
omarchy plugin add <this-repo-url> --enable
```

Then add **Music Assistant** to the bar in the shell's bar settings if it does
not appear.

## Connect

The token is read from your terminal without echo and sent to the keyring on
stdin; it is never placed on a command line or in a file.

```
cd ~/.config/omarchy/plugins/music-assistant
read -rs MA_TOKEN; printf '%s\n' "$MA_TOKEN" | python3 scripts/ma_backend.py setup http://YOUR-SERVER:8095; unset MA_TOKEN
```

Setup checks the token against your server before saving anything. Only the
server address is written to `~/.config/omarchy-music-assistant/config.json`.

By default the plugin controls the Music Assistant player that matches this
computer's hostname (the Companion app). To control a different player:

```
python3 scripts/ma_backend.py players            # list players and their ids
python3 scripts/ma_backend.py set-player <id>
```

To disconnect and remove the saved token and settings:

```
python3 scripts/ma_backend.py disconnect
```

## Privacy and security

- The token lives only in the system keyring.
- The plugin talks only to the server address you give it.
- Artwork addresses returned by some providers can embed access tokens; the
  plugin does not log them.

## Development

```
python3 -I -m unittest discover -s tests -v
omarchy plugin validate .
```

`MA_PLUGIN_URL` and `MA_PLUGIN_TOKEN` environment variables override the saved
settings for local testing only.

## License

MIT. See [LICENSE](LICENSE). The bar-widget structure follows the conventions of
other Omarchy plugins such as
[audiobookshelf-player](https://github.com/jtcarrasco/audiobookshelf-player).
