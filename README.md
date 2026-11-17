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
- **Audiobooks:** a chapter line with previous / next chapter (previous restarts
  the chapter first, like a CD player), a **Chapters** list to jump anywhere, and
  **Bookmarks**: see the bookmarks you already made in Audiobookshelf, add one at
  the current position (with an optional name), jump to one, or delete it.
  Bookmarks read and write your Audiobookshelf account directly, so they stay in
  step with the Audiobookshelf apps. Music Assistant itself has no bookmark
  feature.
- **Resume:** the Continue list shows time left and a progress bar.
- A connect form in the dropdown (gear icon) if you prefer it to the command line.
- Keyboard: `Space` play / pause, `/` search, `Esc` back or close.
- **Speakers and groups**: the speaker button lists every player that can join the one
  you're controlling. Tick a speaker to play on it together with the leader, untick to
  remove it, and use each row's slider for per-speaker volume. "Save these speakers as a
  group…" creates a real Music Assistant group player, so it also
  appears in Music Assistant's own apps and in the player picker here. Saved groups are
  listed as buttons: click one to control it, click the cross twice to delete it (only
  group players can be deleted from here, never a real speaker). Members of a saved group
  can be ticked on and off like any other group. Groups made in the Sonos app are picked
  up too. "Ungroup" dissolves an ad-hoc group. When the ticked speakers are different
  brands (and Music Assistant's Universal Group Player provider is enabled) the plugin offers
  a "Mixed brands" group type; those groups play the same audio but are not sample-synced.
- **Podcasts tab**: shows your unfinished episodes first, then the newest episodes across all
  your shows (with how long ago they came out), then the shows themselves. Click a show to
  see its own episodes.
- **Mark as played / unplayed**: right-click an audiobook or podcast episode. "Reset progress"
  appears for items you've started. For Audiobookshelf items the reset is also sent to
  Audiobookshelf (Music Assistant doesn't forward it), so connect Audiobookshelf in Settings.
- **Sleep timer**: the alarm-clock button opens 15 / 30 / 45 / 60 / 90 minute presets, a custom
  minutes box and (for audiobooks) "End of chapter". The bar shows the time left and the
  timer keeps running with the dropdown closed. When it ends playback is paused, not
  stopped, so your queue and position are kept.
- IPC for keybindings: `omarchy-shell music-assistant toggle | playPause | next | previous | queue | chapters | bookmarks | sleepMenu | speakers | library`, `tab <home|albums|playlists|audiobooks|podcasts>`, plus `sleep <minutes>` and `sleepCancel`.

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

### Audiobookshelf (optional, for bookmarks)

Create an API token in Audiobookshelf (Settings, then Users, then your user) and
enter it under the gear icon in the dropdown, or:

```
read -rs ABS_TOKEN; printf '%s\n' "$ABS_TOKEN" | python3 scripts/ma_backend.py abs-setup https://YOUR-AUDIOBOOKSHELF; unset ABS_TOKEN
```

The token is stored in the system keyring, separate from the Music Assistant
token. Bookmarks only work for audiobooks that come from Audiobookshelf.

### Music Assistant

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
