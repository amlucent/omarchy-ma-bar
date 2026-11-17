# Music Assistant for Omarchy

Control a [Music Assistant](https://www.music-assistant.io/) server from the
Omarchy bar. Early development (v0.2): a bar icon plus a dropdown with
now-playing controls, a player picker, Home / Albums / Playlists / Books /
Podcasts tabs, and search across music, audiobooks and podcasts.

*An independent client. Not affiliated with or endorsed by the Music Assistant
project or the Omarchy project.*

## What works today

- Bar icon showing whether your player is playing, with a tooltip of the current
  title and artist/author. Left click opens the dropdown, middle click plays /
  pauses, right click skips to the next item.
- Dropdown: cover, title, seek bar, previous / play / next, volume, and playback
  speed for audiobooks and podcasts; a picker for which Music Assistant player to
  control; **Home** (continue and recently played), **Albums**, **Playlists**,
  **Books** and **Podcasts** tabs; search; click a podcast to see its episodes.
- **Play queue:** the queue icon next to the transport buttons shows what's up
  next. Click an item to jump to it, move it up or down, remove it, or clear the
  queue.
- **Right-click** any album, playlist, track, audiobook or episode for
  **Play now**, **Play next** and **Add to queue**.
- A connect form in the dropdown (gear icon) if you prefer it to the command line.
- Keyboard: `Space` play / pause, `/` search, `Esc` back or close.
- IPC for keybindings: `omarchy-shell music-assistant toggle | playPause | next | previous | queue | library`.

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
