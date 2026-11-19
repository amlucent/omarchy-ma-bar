# Changelog

## 0.11.2

- **Security:** requests to Music Assistant and Audiobookshelf no longer follow a redirect to
  a different host (scheme, host or port), so the bearer token can never be forwarded to a
  server other than the one you configured. Same-server redirects still work, and a blocked
  redirect shows a clear message asking you to use the address it redirects to. Reported in
  the marketplace review; covered by new tests that run two local servers.

## 0.11.1

- The plugin id is now `io.github.amlucent.ma-bar` (a namespaced id, as the Omarchy plugin marketplace asks for).
  The short IPC target is unchanged: keybindings still use `omarchy-shell ma-bar ...`. If you
  installed 0.11.0, remove it and add the plugin again.
- README: uninstall instructions.

## 0.11.0

- **Renamed to MA Bar.** The plugin id is now `ma-bar`, the repository is
  `amlucent/omarchy-ma-bar` (the old address redirects), and the IPC target is `ma-bar`:
  use `omarchy-shell ma-bar ...` in keybindings. Your saved settings and keyring tokens carry
  over unchanged. If you installed an earlier version, remove it and add the plugin again.

## 0.10.0

- Shuffle and repeat (off / queue / one) and a favorite heart on the playing track or book.
- Favorites: right-click to add or remove, and a Favorites tab.
- Move playback to another speaker with one click (from the Speakers view).
- Keyboard navigation: arrow keys and Enter in every list, Delete to remove, Ctrl+arrows to
  reorder the queue, number keys and Tab to switch tabs.
- README: comparison with the other Music Assistant plugin, documented media-key bindings.

## 0.9.1

- Queue: the playing item is pinned at the top and no longer shows move/remove buttons;
  already-played items are hidden; each upcoming item gets a "play this now" button.
- Automatically finds a hostname-named "universal player" (used by the optional lightweight
  local player), and documents that setup as experimental.

## 0.9.0 (first public beta)

- Bar icon and dropdown for Music Assistant: now playing, transport, volume, speed,
  Home / Albums / Playlists / Books / Podcasts tabs, search.
- Podcasts tab: unfinished episodes first, then the newest episodes across all shows.
- Play queue view with jump, reorder, remove and clear; right-click for
  Play now / Play next / Add to queue.
- Speakers and groups: ad-hoc grouping, per-speaker volume, saved groups created as real
  Music Assistant group players (in sync, or mixed brands via Universal Group).
- Audiobooks: chapters, resume progress, mark as played / unplayed, and Audiobookshelf
  bookmarks (optional Audiobookshelf connection).
- Sleep timer (presets, custom minutes, end of chapter) that pauses playback.
- Prompts for a player when none is chosen and this computer has no Companion player.
