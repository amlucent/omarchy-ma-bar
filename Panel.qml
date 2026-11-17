import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// The dropdown: now-playing controls on top, then Home / Albums / Playlists /
// Audiobooks / Podcasts below, with search. Built from the shell's own qs.Ui
// kit so colours, fonts and spacing follow the current Omarchy theme.
Panel {
  id: root
  moduleName: "music-assistant"
  ipcTarget: "music-assistant"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")

  property int panelWidth: 520
  property int panelHeight: 760

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color mutedFg: Qt.darker(fg, 1.5)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // ---- connection ---------------------------------------------------------
  property bool configured: false
  property bool settingsView: false
  property string serverUrl: ""
  property string setupError: ""
  property bool setupBusy: false
  property bool confirmDisconnect: false
  property string pendingToken: ""

  // ---- player state (from `now --full`) -----------------------------------
  property string playerId: ""
  property string playerName: ""
  property var players: []
  property string state: "idle"
  readonly property bool playing: state === "playing"
  property string title: ""
  property string subtitle: ""
  property string itemType: ""
  property string cover: ""
  property real duration: 0
  property real position: 0
  property real volume: 0
  property real speed: 1.0
  readonly property bool hasItem: title !== ""
  readonly property bool hasSpeed: itemType === "audiobook" || itemType === "podcast_episode"

  // ---- browsing -----------------------------------------------------------
  property string tab: "home"        // home | albums | playlists | audiobooks | podcasts
  property string query: ""
  property var openPodcast: null     // {uri, name} while viewing a show's episodes
  property var rows: []              // [{kind:"header"|"item", ...}]
  property bool loading: false
  property string listError: ""

  // ---- queue + context menu -----------------------------------------------
  property bool queueView: false
  property var queueRows: []
  property string notice: ""
  property var menuItem: null        // the row the context menu is open for
  property real menuX: 0
  property real menuY: 0

  // ---- chapters, bookmarks, Audiobookshelf ---------------------------------
  property bool chaptersView: false
  property bool bookmarksView: false
  property bool sleepView: false
  // ---- speakers / grouping ------------------------------------------------
  property bool speakersView: false
  property var speakers: []
  property string speakersLeaderName: ""
  property bool speakersCanGroup: true
  property string speakersError: ""
  property bool needsPlayer: false
  property bool pickerPrompted: false
  property bool speakersIsGroup: false
  property bool mixedGroupsAvailable: false
  property string groupKindChoice: ""
  readonly property bool membersMixed: {
    var seen = {}, n = 0
    for (var i = 0; i < speakers.length; i++) {
      if (!speakers[i].member) continue
      var k = String(speakers[i].provider).split("--")[0]
      if (!seen[k]) { seen[k] = true; n++ }
    }
    return n > 1
  }
  readonly property string groupKind: groupKindChoice !== "" ? groupKindChoice
    : (membersMixed && mixedGroupsAvailable ? "universal_group" : "sync_group")
  property string confirmDeleteId: ""
  readonly property var groupPlayers: root.playerEntries.filter(function(p) { return p.isGroup })
  property var playerEntries: []
  property var groupQueue: []
  property double speakersHoldUntil: 0
  property int groupSize: 1
  readonly property int memberCount: speakers.filter(function(r) { return r.member }).length
  readonly property var sleepHost: hostWidget
  readonly property bool sleeping: sleepHost ? sleepHost.sleeping === true : false
  readonly property bool listsHidden: queueView || chaptersView || bookmarksView || sleepView || speakersView
  property string itemUri: ""
  property string detailUri: ""
  property var chapters: []
  property var bookmarks: []
  property string bookmarkError: ""
  property bool absConfigured: false
  property string absUrl: ""
  property string absSetupError: ""
  property bool absSetupBusy: false
  property string pendingAbsToken: ""
  readonly property bool isBook: itemType === "audiobook"
  readonly property int chapterIndex: {
    var idx = -1
    for (var i = 0; i < root.chapters.length; i++) {
      if (root.chapters[i].start <= root.position + 0.5) idx = i
      else break
    }
    return idx
  }

  readonly property var tabOptions: [
    { value: "home", label: "Home", tooltip: "Continue and recent" },
    { value: "albums", label: "Albums" },
    { value: "playlists", label: "Playlists" },
    { value: "audiobooks", label: "Books" },
    { value: "podcasts", label: "Podcasts" }
  ]
  readonly property var tabTypes: ({ albums: "album", playlists: "playlist", audiobooks: "audiobook", podcasts: "podcast" })
  readonly property var kindTitles: ({ tracks: "Tracks", albums: "Albums", artists: "Artists", playlists: "Playlists",
                                       audiobooks: "Audiobooks", podcasts: "Podcasts" })
  readonly property var typeLabels: ({ album: "Album", playlist: "Playlist", audiobook: "Audiobook", podcast: "Podcast",
                                       podcast_episode: "Podcast episode", track: "Track", artist: "Artist" })

  function backend(args) {
    return ["python3", root.pluginDir + "/scripts/ma_backend.py"].concat(args)
  }

  function formatTime(sec) {
    sec = Math.max(0, Math.round(sec || 0))
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
    var mm = (h > 0 && m < 10 ? "0" : "") + m
    return (h > 0 ? h + ":" : "") + mm + ":" + (s < 10 ? "0" : "") + s
  }

  function open() { root.controller.show(); root.refreshAll() }
  function close() { root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }

  function goBack() {
    if (root.menuItem) { root.menuItem = null; return true }
    if (root.settingsView && root.configured) { root.settingsView = false; return true }
    if (root.listsHidden) { root.showView(""); return true }
    if (root.openPodcast) { root.openPodcast = null; root.loadList(); return true }
    if (root.query !== "") { searchField.text = ""; return true }
    return false
  }

  function showView(name) {
    root.queueView = name === "queue"
    root.chaptersView = name === "chapters"
    root.bookmarksView = name === "bookmarks"
    root.sleepView = name === "sleep"
    root.speakersView = name === "speakers"
  }
  function toggleView(name) {
    var open = (name === "queue" && root.queueView) || (name === "chapters" && root.chaptersView)
      || (name === "bookmarks" && root.bookmarksView) || (name === "sleep" && root.sleepView)
      || (name === "speakers" && root.speakersView)
    root.showView(open ? "" : name)
  }

  function loadSpeakers() {
    if (!speakersProc.running) { speakersProc.command = root.backend(["speakers", root.playerId]); speakersProc.running = true }
  }
  function groupCmd(args) {
    root.groupQueue = root.groupQueue.concat([args])
    root.runGroupQueue()
  }
  function runGroupQueue() {
    if (groupProc.running || root.groupQueue.length === 0) return
    var next = root.groupQueue[0]
    root.groupQueue = root.groupQueue.slice(1)
    groupProc.lastCmd = next[0]
    groupProc.command = root.backend(next)
    groupProc.running = true
  }
  function patchSpeaker(id, changes) {
    root.speakersHoldUntil = Date.now() + 3000   // don't let a stale poll undo the click
    root.speakers = root.speakers.map(function(r) {
      if (r.id !== id) return r
      var o = {}
      for (var k in r) o[k] = r[k]
      for (var c in changes) o[c] = changes[c]
      return o
    })
  }
  function toggleSpeaker(row) {
    if (row.leader || !root.speakersCanGroup) return
    root.speakersError = ""
    root.patchSpeaker(row.id, { member: !row.member })
    root.groupCmd(["group", root.playerId, row.member ? "remove" : "add", row.id])
  }
  function setSpeakerVolume(row, v) {
    root.patchSpeaker(row.id, { volume: v })
    root.groupCmd(["player-volume", row.id, String(Math.round(v))])
  }
  function toggleSpeakerMute(row) {
    root.patchSpeaker(row.id, { muted: !row.muted })
    root.groupCmd(["player-mute", row.id, row.muted ? "0" : "1"])
  }
  function ungroupAll() {
    root.speakersError = ""
    root.speakers = root.speakers.map(function(r) {
      var o = {}
      for (var k in r) o[k] = r[k]
      if (!r.leader) o.member = false
      return o
    })
    root.speakersHoldUntil = Date.now() + 3000
    root.groupCmd(["ungroup", root.playerId])
  }
  function saveGroup() {
    var name = groupName.text.trim()
    if (name === "") { root.speakersError = "Give the group a name first."; return }
    var ids = root.speakers.filter(function(r) { return r.member }).map(function(r) { return r.id })
    root.speakersError = ""
    root.groupCmd(["group-save", root.groupKind, name].concat(ids))
    groupName.text = ""
    root.groupKindChoice = ""
  }
  function pickPlayer(id) {
    setPlayerProc.command = root.backend(["set-player", id])
    setPlayerProc.running = true
  }
  function deleteGroup(id) {
    if (root.confirmDeleteId !== id) { root.confirmDeleteId = id; confirmReset.restart(); return }
    root.confirmDeleteId = ""
    root.groupCmd(["group-delete", id])
  }

  function startSleep(minutes, label) {
    if (!root.sleepHost || !(minutes > 0)) return
    root.sleepHost.startSleep(minutes * 60, label || (Math.round(minutes) + " min"))
    root.notice = "Sleep timer set: pausing in " + root.sleepHost.formatLeft(Math.round(minutes * 60))
    root.showView("")
  }
  function sleepUntilChapterEnd() {
    if (!root.sleepHost || root.chapterIndex < 0) return
    var left = (root.chapterEnd(root.chapterIndex) - root.position) / Math.max(0.5, root.speed)
    if (!(left > 1)) return
    root.sleepHost.startSleep(left, "end of chapter")
    root.notice = "Sleep timer set: pausing at the end of this chapter"
    root.showView("")
  }
  function cancelSleep() {
    if (root.sleepHost) root.sleepHost.cancelSleep()
    root.notice = "Sleep timer cancelled"
    root.showView("")
  }

  function seekTo(seconds) { root.position = seconds; root.ctl(["seek", String(Math.round(seconds))]) }

  function chapterEnd(i) {
    var c = root.chapters[i]
    return c.end !== null && c.end !== undefined ? c.end : (i + 1 < root.chapters.length ? root.chapters[i + 1].start : root.duration)
  }

  function skipChapter(direction) {
    if (root.chapters.length === 0) return
    var i = root.chapterIndex
    if (direction > 0) {
      if (i + 1 < root.chapters.length) root.seekTo(root.chapters[i + 1].start)
    } else if (i >= 0 && root.position - root.chapters[i].start > 3) {
      root.seekTo(root.chapters[i].start)       // restart the chapter first, like a CD player
    } else if (i > 0) {
      root.seekTo(root.chapters[i - 1].start)
    } else {
      root.seekTo(0)
    }
  }

  function loadBookmarks() {
    if (!root.isBook || !root.absConfigured || root.itemUri === "") { root.bookmarks = []; return }
    root.bookmarkError = ""
    bookmarksProc.command = root.backend(["bookmarks", root.itemUri])
    bookmarksProc.running = true
  }

  function addBookmark() {
    if (!root.isBook || root.itemUri === "") return
    bookmarkActProc.command = root.backend(["bookmark-add", root.itemUri, String(Math.floor(root.position)), bookmarkTitle.text.trim()])
    bookmarkActProc.running = true
    bookmarkTitle.text = ""
    root.showNotice("Bookmark added at " + root.formatTime(root.position))
    bookmarkReload.restart()
  }

  function deleteBookmark(b) {
    bookmarkActProc.command = root.backend(["bookmark-delete", root.itemUri, String(b.time)])
    bookmarkActProc.running = true
    bookmarkReload.restart()
  }

  function goBookmark(b) {
    bookmarkActProc.command = root.backend(["bookmark-go", root.itemUri, String(b.time)])
    bookmarkActProc.running = true
    root.showNotice("Jumping to " + root.formatTime(b.time))
    refreshSoon.restart()
  }

  function showNotice(text) { root.notice = text; noticeTimer.restart() }

  function loadQueue() {
    if (!root.configured || queueProc.running) return
    queueProc.command = root.backend(["queue"])
    queueProc.running = true
  }

  // Run a queue edit, then re-read the queue shortly after the server applies it.
  function queueAction(args, message) {
    queueActProc.command = root.backend(args)
    queueActProc.running = true
    if (message) root.showNotice(message)
    queueRefresh.restart()
  }

  function enqueue(item, mode) {
    root.menuItem = null
    var labels = { play: "Playing now", next: "Playing next", add: "Added to queue" }
    root.queueAction(["enqueue", mode, item.uri], labels[mode] + ": " + item.name)
    if (mode === "play") refreshSoon.restart()
  }

  function openMenu(item, x, y) {
    root.menuItem = item
    root.menuX = x
    root.menuY = y
  }

  // With no player chosen, open the picker once per time the panel is opened.
  function promptPicker() {
    if (!root.needsPlayer || !root.opened || root.pickerPrompted || root.players.length === 0 || root.settingsView) return
    root.pickerPrompted = true
    Qt.callLater(function() { playerPicker.open() })
  }
  onOpenedChanged: if (!root.opened) root.pickerPrompted = false

  function playersProcRun() { playersProc.running = true }

  function refreshAll() {
    absStatusProc.running = true
    if (!root.configured) { statusProc.running = true; return }
    root.pollNow()
    playersProc.running = true
    root.loadList()
  }

  function pollNow() {
    if (!nowProc.running) { nowProc.command = root.backend(["now", "--full"]); nowProc.running = true }
  }

  function ctl(args) {
    ctlProc.command = root.backend(["ctl"].concat(args))
    ctlProc.running = true
    refreshSoon.restart()
  }

  function togglePause() { root.ctl(["playpause"]) }

  function loadList() {
    if (!root.configured) return
    var args
    if (root.openPodcast) args = ["episodes", root.openPodcast.uri]
    else if (root.query !== "") args = ["search", root.query]
    else if (root.tab === "home") args = ["home"]
    else args = ["library", root.tabTypes[root.tab], "60", "0"]
    root.loading = true
    root.listError = ""
    listProc.command = root.backend(args)
    listProc.running = true
  }

  // Turn whatever the backend returned into one flat list of rows.
  function buildRows(result) {
    var out = []
    function addSection(title, items) {
      if (!items || items.length === 0) return
      if (title) out.push({ kind: "header", title: title })
      for (var i = 0; i < items.length; i++) { var it = items[i]; it.kind = "item"; out.push(it) }
    }
    if (Array.isArray(result)) {
      addSection("", result)
    } else if (result.continue !== undefined || result.recent !== undefined) {
      addSection("Continue", result.continue)
      addSection("Recently played", result.recent)
    } else {
      var order = ["tracks", "albums", "artists", "playlists", "audiobooks", "podcasts"]
      for (var k = 0; k < order.length; k++) addSection(root.kindTitles[order[k]], result[order[k]])
    }
    return out
  }

  function activate(item) {
    if (item.type === "podcast") {
      root.openPodcast = { uri: item.uri, name: item.name }
      root.loadList()
      return
    }
    playProc.command = root.backend(["play", item.uri])
    playProc.running = true
    refreshSoon.restart()
  }

  onTabChanged: { root.openPodcast = null; root.loadList() }
  onQueueViewChanged: if (root.queueView) root.loadQueue()
  onSpeakersViewChanged: if (root.speakersView) root.loadSpeakers()
  onBookmarksViewChanged: if (root.bookmarksView) root.loadBookmarks()
  onAbsConfiguredChanged: if (root.bookmarksView) root.loadBookmarks()
  onQueryChanged: searchDebounce.restart()

  Timer { id: searchDebounce; interval: 350; onTriggered: { root.openPodcast = null; root.loadList() } }
  // The server reports new state a second or two after a command.
  Timer { id: refreshSoon; interval: 1500; onTriggered: root.pollNow() }
  Timer {
    interval: 2000
    running: root.opened && root.configured
    repeat: true
    onTriggered: {
      root.pollNow()
      if (root.queueView) root.loadQueue()
      if (root.speakersView && Date.now() > root.speakersHoldUntil) root.loadSpeakers()
    }
  }
  Timer { id: bookmarkReload; interval: 900; onTriggered: root.loadBookmarks() }
  Timer { id: noticeTimer; interval: 2500; onTriggered: root.notice = "" }
  Timer { id: queueRefresh; interval: 700; onTriggered: { root.loadQueue(); root.pollNow() } }
  // Smooth the progress bar between server updates.
  Timer {
    interval: 1000
    running: root.opened && root.playing
    repeat: true
    onTriggered: if (root.duration <= 0 || root.position < root.duration) root.position += root.speed
  }

  Component.onCompleted: { statusProc.running = true; absStatusProc.running = true }

  // ---- backend processes --------------------------------------------------
  Process {
    id: statusProc
    command: root.backend(["status"])
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          root.configured = r.configured === true
          root.serverUrl = r.serverUrl || ""
          if (!root.configured) root.settingsView = true
          else if (root.opened) root.refreshAll()
        } catch (e) { root.setupError = "Could not read the plugin status" }
      }
    }
  }

  Process {
    id: nowProc
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          if (r.error) {
            root.needsPlayer = r.code === "no_player"
            if (root.needsPlayer) root.promptPicker()
            return
          }
          root.needsPlayer = false
          root.state = r.state || "idle"
          root.playerId = r.id || root.playerId
          root.groupSize = r.groupSize || 1
          root.playerName = r.player || root.playerName
          root.volume = r.volume === undefined || r.volume === null ? root.volume : r.volume
          root.speed = r.speed || 1.0
          if (r.item) {
            root.title = r.item.name || ""
            root.subtitle = r.item.subtitle || ""
            root.itemType = r.item.type || ""
            root.itemUri = r.item.uri || ""
            root.cover = r.item.cover || ""
            root.duration = r.item.duration || 0
          } else {
            root.title = ""; root.subtitle = ""; root.itemType = ""; root.cover = ""; root.duration = 0
            root.itemUri = ""
          }
          // Chapters (and the book's Audiobookshelf id) load once per audiobook.
          if (root.isBook && root.itemUri !== "" && root.itemUri !== root.detailUri) {
            root.detailUri = root.itemUri
            root.chapters = []
            root.bookmarks = []
            detailsProc.command = root.backend(["details", "audiobook", root.itemUri])
            detailsProc.running = true
          } else if (!root.isBook && root.detailUri !== "") {
            root.detailUri = ""
            root.chapters = []
            root.bookmarks = []
          }
          root.position = r.elapsed || 0
        } catch (e) {}
      }
    }
  }

  Process { id: ctlProc }
  Process {
    id: speakersProc
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          if (r.error) { root.speakersError = r.error; return }
          if (Date.now() < root.speakersHoldUntil) return
          root.speakers = r.speakers || []
          root.speakersLeaderName = r.leaderName || ""
          root.speakersCanGroup = r.canGroup !== false
          root.speakersIsGroup = r.isGroup === true
          root.mixedGroupsAvailable = r.mixedGroups === true
        } catch (e) {}
      }
    }
  }
  Process {
    id: groupProc
    property string lastCmd: ""
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          if (r.error) root.speakersError = r.error
          else if (r.missing && r.missing.length > 0) root.notice = r.missing.length + " speaker(s) in that group are offline"
        } catch (e) { root.speakersError = "Could not change the group" }
      }
    }
    onExited: {
      if (lastCmd === "group-save" || lastCmd === "group-delete") root.refreshAll()
      speakersSoon.restart()
      root.runGroupQueue()
    }
  }
  Timer { id: confirmReset; interval: 4000; onTriggered: root.confirmDeleteId = "" }
  Timer {
    id: speakersSoon
    interval: 1500
    onTriggered: {
      if (root.groupQueue.length > 0 || groupProc.running) return
      root.speakersHoldUntil = 0
      root.pollNow()
      root.playersProcRun()
      if (root.speakersView) root.loadSpeakers()
    }
  }
  Process { id: queueActProc }
  Process { id: bookmarkActProc }
  Process {
    id: detailsProc
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          root.chapters = r.error ? [] : (r.chapters || [])
          if (root.bookmarksView) root.loadBookmarks()
        } catch (e) { root.chapters = [] }
      }
    }
  }
  Process {
    id: bookmarksProc
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          if (r.error) { root.bookmarkError = r.error; root.bookmarks = [] }
          else root.bookmarks = r
        } catch (e) { root.bookmarkError = "Could not read your bookmarks" }
      }
    }
  }
  Process {
    id: absStatusProc
    command: root.backend(["abs-status"])
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          root.absConfigured = r.configured === true
          root.absUrl = r.absUrl || ""
        } catch (e) {}
      }
    }
  }
  Process {
    id: absSetupProc
    stdinEnabled: true
    onStarted: { write(root.pendingAbsToken + "\n"); root.pendingAbsToken = "" }
    stdout: StdioCollector {
      onStreamFinished: {
        root.absSetupBusy = false
        try {
          var r = JSON.parse(text)
          if (r.ok) { root.absConfigured = true; root.absUrl = r.absUrl; absTokenField.text = ""; root.absSetupError = "" }
          else root.absSetupError = r.error || "Connection failed"
        } catch (e) { root.absSetupError = "Unexpected response from the plugin" }
      }
    }
  }
  Process {
    id: absDisconnectProc
    command: root.backend(["abs-disconnect"])
    stdout: StdioCollector { onStreamFinished: { root.absConfigured = false; root.absUrl = ""; root.bookmarks = [] } }
  }
  Process {
    id: queueProc
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          if (Array.isArray(r)) root.queueRows = r
        } catch (e) {}
      }
    }
  }
  Process { id: playProc }
  Process { id: setPlayerProc; onExited: root.refreshAll() }

  Process {
    id: playersProc
    command: root.backend(["players"])
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var r = JSON.parse(text)
          if (r.error) return
          var list = r.filter(function(p) { return p.available })
          list.sort(function(a, b) {
            var sa = a.provider === "sendspin" ? 0 : 1, sb = b.provider === "sendspin" ? 0 : 1
            return sa !== sb ? sa - sb : a.name.localeCompare(b.name)
          })
          root.playerEntries = list
          Qt.callLater(root.promptPicker)
          root.players = list.map(function(p) {
            var extra = p.isGroup ? "  (group)" : (p.members && p.members.length > 1 ? "  +" + (p.members.length - 1) : "")
            return { value: p.id, label: p.name + extra, base: p.name }
          })
        } catch (e) {}
      }
    }
  }

  Process {
    id: listProc
    stdout: StdioCollector {
      onStreamFinished: {
        root.loading = false
        try {
          var r = JSON.parse(text)
          if (r.error) { root.listError = r.error; root.rows = []; return }
          root.rows = root.buildRows(r)
        } catch (e) { root.listError = "Could not read the library"; root.rows = [] }
      }
    }
  }

  Process {
    id: setupProc
    // stdin stays enabled: the backend reads the token from one line of it, so
    // the token never appears in a command line.
    stdinEnabled: true
    onStarted: { write(root.pendingToken + "\n"); root.pendingToken = "" }
    stdout: StdioCollector {
      onStreamFinished: {
        root.setupBusy = false
        try {
          var r = JSON.parse(text)
          if (r.ok) {
            root.configured = true
            root.serverUrl = r.serverUrl
            root.settingsView = false
            tokenField.text = ""
            root.refreshAll()
          } else {
            root.setupError = r.error || "Connection failed"
          }
        } catch (e) { root.setupError = "Unexpected response from the plugin" }
      }
    }
  }

  Process {
    id: disconnectProc
    command: root.backend(["disconnect"])
    stdout: StdioCollector {
      onStreamFinished: {
        root.configured = false
        root.serverUrl = ""
        root.rows = []
        root.title = ""
        root.confirmDisconnect = false
        root.settingsView = true
      }
    }
  }
  Timer { id: confirmTimer; interval: 4000; onTriggered: root.confirmDisconnect = false }

  // ---- UI -----------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(root.panelWidth)
    contentHeight: panel.fittedContentHeight(root.panelHeight)
    padding: Style.space(20)

    Item { id: slot; anchors.fill: parent }
  }

  PanelKeyCatcher {
    id: keyCatcher
    parent: slot
    anchors.fill: parent
    blocked: searchField.activeFocus || urlField.activeFocus || tokenField.activeFocus
    onCloseRequested: { if (!root.goBack()) root.close() }
    onActivateRequested: root.togglePause()
    onTextKey: function(t) { if (t === "/") searchField.forceActiveFocus() }

    ColumnLayout {
      id: mainCol
      anchors.fill: parent
      spacing: Style.space(12)

      // ---- header ---------------------------------------------------------
      RowLayout {
        Layout.fillWidth: true
        spacing: Style.spacing.md

        PanelActionButton {
          visible: root.openPodcast !== null || (root.settingsView && root.configured)
          iconText: "󰁍"
          tooltipText: "Back (Esc)"
          foreground: root.fg
          onClicked: root.goBack()
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: root.settingsView ? (root.configured ? "Settings" : "Connect to Music Assistant")
            : (root.openPodcast ? root.openPodcast.name : "Music Assistant")
          color: root.fg
          font.family: root.fontFamily
          font.pixelSize: Style.font.heading
          font.bold: true
          elide: Text.ElideRight
        }

        Dropdown {
          id: playerPicker
          visible: root.configured && !root.settingsView && root.players.length > 0
          Layout.preferredWidth: Style.space(190)
          showLabel: false
          options: root.players
          value: root.playerId
          foreground: root.fg
          fontFamily: root.fontFamily
          onChanged: function(v) {
            setPlayerProc.command = root.backend(["set-player", v])
            setPlayerProc.running = true
          }
        }

        PanelActionButton {
          visible: root.configured && !root.settingsView
          iconText: "󰑐"
          tooltipText: "Refresh"
          foreground: root.fg
          onClicked: root.refreshAll()
        }

        PanelActionButton {
          visible: root.configured
          iconText: "󰒓"
          tooltipText: "Settings"
          foreground: root.fg
          onClicked: { root.settingsView = !root.settingsView; if (root.settingsView) urlField.text = root.serverUrl }
        }
      }

      Text {
        textFormat: Text.PlainText
        visible: root.notice !== ""
        Layout.fillWidth: true
        text: root.notice
        color: Color.accent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        elide: Text.ElideRight
      }

      // ---- connect / settings ---------------------------------------------
      ColumnLayout {
        visible: root.settingsView
        Layout.fillWidth: true
        spacing: Style.space(10)

        PanelSectionHeader { text: "SERVER"; foreground: root.fg; fontFamily: root.fontFamily }
        TextField {
          id: urlField
          Layout.fillWidth: true
          placeholderText: "http://your-server:8095"
          foreground: root.fg
          font.family: root.fontFamily
        }
        PanelSectionHeader { text: "LONG-LIVED TOKEN"; foreground: root.fg; fontFamily: root.fontFamily }
        TextField {
          id: tokenField
          Layout.fillWidth: true
          placeholderText: "Music Assistant > Settings > Profile > Long-lived access token"
          password: true
          foreground: root.fg
          font.family: root.fontFamily
        }
        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          wrapMode: Text.WordWrap
          text: "The token is stored in your system keyring, not in a file."
          color: root.mutedFg
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
        Text {
          textFormat: Text.PlainText
          visible: root.setupError !== ""
          Layout.fillWidth: true
          wrapMode: Text.WordWrap
          text: root.setupError
          color: Color.accent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
        RowLayout {
          spacing: Style.spacing.md
          Button {
            text: root.setupBusy ? "Connecting..." : "Connect"
            bordered: true
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: {
              if (root.setupBusy) return
              root.setupError = ""
              root.setupBusy = true
              root.pendingToken = tokenField.text.trim()
              setupProc.command = root.backend(["setup", urlField.text.trim()])
              setupProc.running = true
            }
          }
          Button {
            visible: root.configured
            text: root.confirmDisconnect ? "Click again to disconnect" : "Disconnect"
            bordered: true
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: {
              if (root.confirmDisconnect) disconnectProc.running = true
              else { root.confirmDisconnect = true; confirmTimer.restart() }
            }
          }
        }

        PanelSectionHeader {
          visible: root.configured
          text: "AUDIOBOOKSHELF  (for bookmarks)"
          foreground: root.fg
          fontFamily: root.fontFamily
        }
        Text {
          textFormat: Text.PlainText
          visible: root.configured
          Layout.fillWidth: true
          wrapMode: Text.WordWrap
          text: root.absConfigured ? "Connected to " + root.absUrl
            : "Optional. Create an API token in Audiobookshelf (Settings > Users) to see and add your bookmarks."
          color: root.mutedFg
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
        TextField {
          id: absUrlField
          visible: root.configured && !root.absConfigured
          Layout.fillWidth: true
          placeholderText: "https://your-audiobookshelf"
          foreground: root.fg
          font.family: root.fontFamily
        }
        TextField {
          id: absTokenField
          visible: root.configured && !root.absConfigured
          Layout.fillWidth: true
          placeholderText: "Audiobookshelf API token"
          password: true
          foreground: root.fg
          font.family: root.fontFamily
        }
        Text {
          textFormat: Text.PlainText
          visible: root.absSetupError !== ""
          Layout.fillWidth: true
          wrapMode: Text.WordWrap
          text: root.absSetupError
          color: Color.accent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
        RowLayout {
          visible: root.configured
          Button {
            visible: !root.absConfigured
            text: root.absSetupBusy ? "Connecting..." : "Connect Audiobookshelf"
            bordered: true
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: {
              if (root.absSetupBusy) return
              root.absSetupError = ""
              root.absSetupBusy = true
              root.pendingAbsToken = absTokenField.text.trim()
              absSetupProc.command = root.backend(["abs-setup", absUrlField.text.trim()])
              absSetupProc.running = true
            }
          }
          Button {
            visible: root.absConfigured
            text: "Disconnect Audiobookshelf"
            bordered: true
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: absDisconnectProc.running = true
          }
        }
      }

      // ---- now playing ----------------------------------------------------
      ColumnLayout {
        visible: root.configured && !root.settingsView
        Layout.fillWidth: true
        spacing: Style.space(10)

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.spacing.xl

          Rectangle {
            Layout.preferredWidth: Style.space(72)
            Layout.preferredHeight: Style.space(72)
            radius: Style.cornerRadius
            color: Style.hoverFillFor(root.fg, Color.accent)
            clip: true
            Image {
              id: nowCover
              anchors.fill: parent
              visible: root.cover !== "" && status === Image.Ready
              source: root.cover
              sourceSize.width: Style.space(72) * 2
              sourceSize.height: Style.space(72) * 2
              fillMode: Image.PreserveAspectCrop
              asynchronous: true
            }
            Text {
              anchors.centerIn: parent
              // The server has no artwork for some tracks (it answers 404).
              visible: root.cover === "" || nowCover.status !== Image.Ready
              textFormat: Text.PlainText
              text: "󰎆"
              color: root.mutedFg
              font.family: root.fontFamily
              font.pixelSize: Style.font.icon * 1.6
            }
          }

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.spacing.xs
            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              text: root.hasItem ? root.title : (root.needsPlayer ? "Choose a player" : "Nothing playing")
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
            }
            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              text: root.hasItem ? root.subtitle : (root.needsPlayer ? "Pick where to play from the list at the top" : root.playerName !== "" ? "on " + root.playerName + (root.groupSize > 1 ? " +" + (root.groupSize - 1) : "") : "")
              color: root.mutedFg
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              elide: Text.ElideRight
            }
          }
        }

        PanelSlider {
          id: seek
          Layout.fillWidth: true
          visible: root.hasItem && root.duration > 0
          bar: root.bar
          minimum: 0
          maximum: Math.max(1, root.duration)
          step: 1
          value: root.position
          onReleased: function(v) { root.position = v; root.ctl(["seek", String(Math.round(v))]) }
        }
        RowLayout {
          Layout.fillWidth: true
          visible: root.hasItem && root.duration > 0
          Text {
            textFormat: Text.PlainText
            text: root.formatTime(seek.dragging ? seek.liveValue : root.position)
            color: root.mutedFg
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
          Item { Layout.fillWidth: true }
          Text {
            textFormat: Text.PlainText
            text: root.formatTime(root.duration)
            color: root.mutedFg
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.spacing.xl
          Item { Layout.fillWidth: true }
          PanelActionButton { iconText: "󰒮"; tooltipText: "Previous"; foreground: root.fg; onClicked: root.ctl(["previous"]) }
          PanelActionButton {
            iconText: root.playing ? "󰏤" : "󰐊"
            tooltipText: root.playing ? "Pause (Space)" : "Play (Space)"
            foreground: root.fg
            fontSize: Style.font.icon * 1.5
            onClicked: root.togglePause()
          }
          PanelActionButton { iconText: "󰒭"; tooltipText: "Next"; foreground: root.fg; onClicked: root.ctl(["next"]) }
          Item { Layout.fillWidth: true }
          PanelActionButton {
            iconText: "󰲸"
            tooltipText: root.queueView ? "Back to the library" : "Show the play queue"
            foreground: root.queueView ? Color.accent : root.fg
            onClicked: root.toggleView("queue")
          }
          PanelActionButton {
            iconText: "󰓃"
            tooltipText: root.groupSize > 1 ? "Speakers: playing on " + root.groupSize : "Speakers and groups"
            foreground: root.speakersView || root.groupSize > 1 ? Color.accent : root.fg
            onClicked: root.toggleView("speakers")
          }
          PanelActionButton {
            iconText: root.sleeping ? "󰒲" : "󰤄"
            tooltipText: root.sleeping ? "Sleep timer: " + root.sleepHost.formatLeft(root.sleepHost.sleepLeft) + " left" : "Sleep timer"
            foreground: root.sleepView || root.sleeping ? Color.accent : root.fg
            onClicked: root.toggleView("sleep")
          }
        }

        // Audiobooks: chapter navigation and bookmarks.
        RowLayout {
          visible: root.isBook
          Layout.fillWidth: true
          spacing: Style.spacing.md
          PanelActionButton {
            iconText: "󰒮"
            tooltipText: "Previous chapter"
            foreground: root.fg
            onClicked: root.skipChapter(-1)
          }
          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: root.chapterIndex >= 0
              ? "Chapter " + (root.chapterIndex + 1) + " of " + root.chapters.length
                + (root.chapters[root.chapterIndex].name !== "" && !/^chapter\s*\d+$/i.test(root.chapters[root.chapterIndex].name)
                    ? "  ·  " + root.chapters[root.chapterIndex].name : "")
              : (root.chapters.length > 0 ? root.chapters.length + " chapters" : "No chapters")
            color: root.mutedFg
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
          }
          PanelActionButton {
            iconText: "󰒭"
            tooltipText: "Next chapter"
            foreground: root.fg
            onClicked: root.skipChapter(1)
          }
        }
        RowLayout {
          visible: root.isBook
          Layout.fillWidth: true
          spacing: Style.spacing.md
          Item { Layout.fillWidth: true }
          Button {
            text: "Chapters"
            bordered: true
            selected: root.chaptersView
            enabled: root.chapters.length > 0
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: root.toggleView("chapters")
          }
          Button {
            text: "Bookmarks"
            bordered: true
            selected: root.bookmarksView
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: root.toggleView("bookmarks")
          }
          Item { Layout.fillWidth: true }
        }

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.spacing.md
          Text {
            textFormat: Text.PlainText
            text: "󰕾"
            color: root.mutedFg
            font.family: root.fontFamily
            font.pixelSize: Style.font.icon
          }
          PanelSlider {
            Layout.fillWidth: true
            bar: root.bar
            minimum: 0
            maximum: 100
            step: 1
            integer: true
            value: root.volume
            onReleased: function(v) { root.volume = v; root.ctl(["volume", String(Math.round(v))]) }
          }
          Text {
            textFormat: Text.PlainText
            Layout.preferredWidth: Style.space(34)
            horizontalAlignment: Text.AlignRight
            text: Math.round(root.volume) + "%"
            color: root.mutedFg
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        ButtonGroup {
          visible: root.hasSpeed
          Layout.alignment: Qt.AlignHCenter
          options: [
            { value: "1", label: "1×" }, { value: "1.25", label: "1.25×" }, { value: "1.5", label: "1.5×" },
            { value: "1.75", label: "1.75×" }, { value: "2", label: "2×" }
          ]
          value: String(root.speed)
          foreground: root.fg
          fontFamily: root.fontFamily
          focusable: false
          onChanged: function(v) { root.speed = parseFloat(v); root.ctl(["speed", v]) }
        }
      }

      // ---- tabs + search --------------------------------------------------
      ButtonGroup {
        visible: root.configured && !root.settingsView && !root.listsHidden && root.openPodcast === null
        Layout.fillWidth: true
        options: root.tabOptions
        value: root.tab
        foreground: root.fg
        fontFamily: root.fontFamily
        focusable: false
        onChanged: function(v) { searchField.text = ""; root.tab = v }
      }

      TextField {
        id: searchField
        visible: root.configured && !root.settingsView && !root.listsHidden && root.openPodcast === null
        Layout.fillWidth: true
        placeholderText: "Search music, audiobooks and podcasts  ( / )"
        foreground: root.fg
        font.family: root.fontFamily
        onTextChanged: root.query = text.trim()
      }

      Text {
        textFormat: Text.PlainText
        visible: root.configured && !root.settingsView && !root.listsHidden && (root.listError !== "" || (!root.loading && root.rows.length === 0))
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: root.listError !== "" ? root.listError : "Nothing here"
        color: root.listError !== "" ? Color.accent : root.mutedFg
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      // ---- list -----------------------------------------------------------
      ListView {
        id: list
        visible: root.configured && !root.settingsView && !root.listsHidden
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: Style.spacing.xs
        boundsBehavior: Flickable.StopAtBounds
        model: root.rows
        delegate: Item {
          id: del
          required property var modelData
          readonly property bool isHeader: modelData.kind === "header"
          width: list.width
          implicitHeight: isHeader ? Style.space(28) : Style.space(60)
          height: implicitHeight

          PanelSectionHeader {
            visible: del.isHeader
            anchors.verticalCenter: parent.verticalCenter
            text: del.isHeader ? String(del.modelData.title).toUpperCase() : ""
            foreground: root.fg
            fontFamily: root.fontFamily
          }

          Rectangle {
            visible: !del.isHeader
            anchors.fill: parent
            radius: Style.cornerRadius
            color: rowMouse.containsMouse ? Style.hoverFillFor(root.fg, Color.accent) : "transparent"

            RowLayout {
              anchors.fill: parent
              anchors.margins: Style.space(6)
              spacing: Style.spacing.lg

              Rectangle {
                Layout.preferredWidth: Style.space(48)
                Layout.preferredHeight: Style.space(48)
                radius: Style.cornerRadius
                color: Style.hoverFillFor(root.fg, Color.accent)
                clip: true
                Image {
                  anchors.fill: parent
                  visible: !del.isHeader && del.modelData.cover !== ""
                  source: !del.isHeader ? del.modelData.cover : ""
                  sourceSize.width: Style.space(48) * 2
                  sourceSize.height: Style.space(48) * 2
                  fillMode: Image.PreserveAspectCrop
                  asynchronous: true
                }
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.spacing.xxs
                Text {
                  textFormat: Text.PlainText
                  Layout.fillWidth: true
                  text: !del.isHeader ? del.modelData.name : ""
                  color: !del.isHeader && del.modelData.played ? root.mutedFg : root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  elide: Text.ElideRight
                }
                Text {
                  textFormat: Text.PlainText
                  Layout.fillWidth: true
                  text: {
                    if (del.isHeader) return ""
                    var m = del.modelData, parts = []
                    parts.push(m.subtitle !== "" ? m.subtitle : (root.typeLabels[m.type] || ""))
                    if (m.resume && m.duration && m.resume < m.duration) parts.push(root.formatTime(m.duration - m.resume) + " left")
                    else if (m.duration) parts.push(root.formatTime(m.duration))
                    if (m.played) parts.push("Played")
                    return parts.join("  ·  ")
                  }
                  color: root.mutedFg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              Text {
                textFormat: Text.PlainText
                visible: !del.isHeader && del.modelData.type === "podcast"
                text: "󰅂"
                color: root.mutedFg
                font.family: root.fontFamily
                font.pixelSize: Style.font.icon
              }
            }

            Rectangle {
              visible: !del.isHeader && del.modelData.resume > 0 && del.modelData.duration > 0
              anchors.left: parent.left
              anchors.bottom: parent.bottom
              anchors.leftMargin: Style.space(6)
              height: Style.space(2)
              radius: height / 2
              color: Color.accent
              width: visible ? Math.max(0, Math.min(1, del.modelData.resume / del.modelData.duration)) * (parent.width - Style.space(12)) : 0
            }

            MouseArea {
              id: rowMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              acceptedButtons: Qt.LeftButton | Qt.RightButton
              onClicked: function(mouse) {
                if (mouse.button === Qt.RightButton) {
                  if (del.modelData.type === "podcast") return
                  var p = rowMouse.mapToItem(keyCatcher, mouse.x, mouse.y)
                  root.openMenu(del.modelData, p.x, p.y)
                } else {
                  root.activate(del.modelData)
                }
              }
            }
          }
        }
      }

      // ---- queue ----------------------------------------------------------
      RowLayout {
        visible: root.configured && !root.settingsView && root.queueView
        Layout.fillWidth: true
        PanelSectionHeader {
          text: "UP NEXT  ·  " + root.queueRows.length + (root.queueRows.length === 1 ? " ITEM" : " ITEMS")
          foreground: root.fg
          fontFamily: root.fontFamily
        }
        Item { Layout.fillWidth: true }
        Button {
          visible: root.queueRows.length > 0
          text: "Clear"
          iconText: "󰆴"
          bordered: true
          foreground: root.fg
          fontFamily: root.fontFamily
          onClicked: root.queueAction(["queue-clear"], "Queue cleared")
        }
      }

      Text {
        textFormat: Text.PlainText
        visible: root.configured && !root.settingsView && root.queueView && root.queueRows.length === 0
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "The queue is empty. Right-click anything in the library to play it next or add it to the queue."
        color: root.mutedFg
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      ListView {
        id: queueList
        visible: root.configured && !root.settingsView && root.queueView
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: Style.spacing.xs
        boundsBehavior: Flickable.StopAtBounds
        model: root.queueRows
        delegate: Rectangle {
          id: qd
          required property var modelData
          required property int index
          width: queueList.width
          height: Style.space(60)
          radius: Style.cornerRadius
          color: qMouse.containsMouse || qUp.hot || qDown.hot || qDel.hot
            ? Style.hoverFillFor(root.fg, Color.accent)
            : (modelData.current ? Style.selectedFillFor(root.fg, Color.accent) : "transparent")

          Rectangle {
            visible: qd.modelData.current
            width: Style.space(3)
            height: parent.height - Style.space(12)
            anchors.verticalCenter: parent.verticalCenter
            radius: width / 2
            color: Color.accent
          }

          MouseArea {
            id: qMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.queueAction(["queue-jump", qd.modelData.id])
          }

          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(10)
            anchors.rightMargin: Style.space(6)
            anchors.topMargin: Style.space(6)
            anchors.bottomMargin: Style.space(6)
            spacing: Style.spacing.lg

            Rectangle {
              Layout.preferredWidth: Style.space(48)
              Layout.preferredHeight: Style.space(48)
              radius: Style.cornerRadius
              color: Style.hoverFillFor(root.fg, Color.accent)
              clip: true
              Image {
                anchors.fill: parent
                visible: qd.modelData.cover !== ""
                source: qd.modelData.cover
                sourceSize.width: Style.space(48) * 2
                sourceSize.height: Style.space(48) * 2
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
              }
            }

            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.spacing.xxs
              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: qd.modelData.name
                color: qd.modelData.available ? root.fg : root.mutedFg
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: qd.modelData.current
                elide: Text.ElideRight
              }
              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: {
                  var parts = []
                  if (qd.modelData.subtitle !== "") parts.push(qd.modelData.subtitle)
                  if (qd.modelData.duration) parts.push(root.formatTime(qd.modelData.duration))
                  if (qd.modelData.current) parts.push(root.playing ? "Playing" : "Current")
                  return parts.join("  ·  ")
                }
                color: root.mutedFg
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }

            PanelActionButton {
              id: qUp
              visible: qd.index > 0
              iconText: "󰁝"
              tooltipText: "Move up"
              foreground: root.fg
              onClicked: root.queueAction(["queue-move", qd.modelData.id, "-1"])
            }
            PanelActionButton {
              id: qDown
              visible: qd.index < root.queueRows.length - 1
              iconText: "󰁅"
              tooltipText: "Move down"
              foreground: root.fg
              onClicked: root.queueAction(["queue-move", qd.modelData.id, "1"])
            }
            PanelActionButton {
              id: qDel
              iconText: "󰆴"
              tooltipText: "Remove from the queue"
              foreground: root.fg
              onClicked: root.queueAction(["queue-remove", qd.modelData.id])
            }
          }
        }
      }

      // ---- chapters -------------------------------------------------------
      PanelSectionHeader {
        visible: root.configured && !root.settingsView && root.chaptersView
        text: "CHAPTERS  ·  " + root.chapters.length
        foreground: root.fg
        fontFamily: root.fontFamily
      }
      ListView {
        id: chapterList
        visible: root.configured && !root.settingsView && root.chaptersView
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: Style.spacing.xxs
        boundsBehavior: Flickable.StopAtBounds
        model: root.chapters
        // Start with the current chapter in view.
        onVisibleChanged: if (visible && root.chapterIndex >= 0) positionViewAtIndex(Math.max(0, root.chapterIndex - 2), ListView.Beginning)
        delegate: Rectangle {
          id: cd
          required property var modelData
          required property int index
          width: chapterList.width
          height: Style.space(44)
          radius: Style.cornerRadius
          readonly property bool isCurrent: index === root.chapterIndex
          color: cMouse.containsMouse ? Style.hoverFillFor(root.fg, Color.accent)
            : (isCurrent ? Style.selectedFillFor(root.fg, Color.accent) : "transparent")
          Rectangle {
            visible: cd.isCurrent
            width: Style.space(3)
            height: parent.height - Style.space(12)
            anchors.verticalCenter: parent.verticalCenter
            radius: width / 2
            color: Color.accent
          }
          MouseArea {
            id: cMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.seekTo(cd.modelData.start)
          }
          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(12)
            anchors.rightMargin: Style.space(10)
            spacing: Style.spacing.lg
            Text {
              textFormat: Text.PlainText
              Layout.preferredWidth: Style.space(30)
              text: cd.modelData.position
              color: root.mutedFg
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              horizontalAlignment: Text.AlignRight
            }
            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              text: cd.modelData.name
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: cd.isCurrent
              elide: Text.ElideRight
            }
            Text {
              textFormat: Text.PlainText
              text: root.formatTime(cd.modelData.start)
              color: root.mutedFg
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
        }
      }

      // ---- bookmarks ------------------------------------------------------
      RowLayout {
        visible: root.configured && !root.settingsView && root.bookmarksView && root.absConfigured
        Layout.fillWidth: true
        spacing: Style.spacing.md
        TextField {
          id: bookmarkTitle
          Layout.fillWidth: true
          placeholderText: "Bookmark name (optional)"
          foreground: root.fg
          font.family: root.fontFamily
          onAccepted: root.addBookmark()
        }
        Button {
          text: "Add at " + root.formatTime(root.position)
          iconText: "󰐕"
          bordered: true
          foreground: root.fg
          fontFamily: root.fontFamily
          onClicked: root.addBookmark()
        }
      }
      Text {
        textFormat: Text.PlainText
        visible: root.configured && !root.settingsView && root.bookmarksView
          && (!root.absConfigured || root.bookmarkError !== "" || root.bookmarks.length === 0)
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: !root.absConfigured ? "Connect Audiobookshelf in Settings (gear icon) to use your bookmarks."
          : (root.bookmarkError !== "" ? root.bookmarkError : "No bookmarks for this book yet.")
        color: root.bookmarkError !== "" ? Color.accent : root.mutedFg
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
      ListView {
        id: bookmarkList
        visible: root.configured && !root.settingsView && root.bookmarksView && root.absConfigured
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: Style.spacing.xs
        boundsBehavior: Flickable.StopAtBounds
        model: root.bookmarks
        delegate: Rectangle {
          id: bd
          required property var modelData
          width: bookmarkList.width
          height: Style.space(52)
          radius: Style.cornerRadius
          color: bMouse.containsMouse || bDel.hot ? Style.hoverFillFor(root.fg, Color.accent) : "transparent"
          MouseArea {
            id: bMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.goBookmark(bd.modelData)
          }
          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(12)
            anchors.rightMargin: Style.space(6)
            spacing: Style.spacing.lg
            Text {
              textFormat: Text.PlainText
              text: "󰃀"
              color: Color.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.icon
            }
            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.spacing.xxs
              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: bd.modelData.title
                color: root.fg
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: root.formatTime(bd.modelData.time)
                color: root.mutedFg
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
            PanelActionButton {
              id: bDel
              iconText: "󰆴"
              tooltipText: "Delete this bookmark"
              foreground: root.fg
              onClicked: root.deleteBookmark(bd.modelData)
            }
          }
        }
      }

      // ---- sleep timer ----------------------------------------------------
      PanelSectionHeader {
        visible: root.configured && !root.settingsView && root.sleepView
        text: root.sleeping ? "SLEEP TIMER  ·  " + root.sleepHost.formatLeft(root.sleepHost.sleepLeft) + " LEFT" : "SLEEP TIMER"
        foreground: root.fg
        fontFamily: root.fontFamily
      }
      Text {
        textFormat: Text.PlainText
        visible: root.configured && !root.settingsView && root.sleepView
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "Pause playback after:"
        color: root.mutedFg
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
      Flow {
        visible: root.configured && !root.settingsView && root.sleepView
        Layout.fillWidth: true
        spacing: Style.spacing.md
        Repeater {
          model: [15, 30, 45, 60, 90]
          delegate: Button {
            required property int modelData
            text: modelData + " min"
            bordered: true
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: root.startSleep(modelData)
          }
        }
        Button {
          visible: root.chapterIndex >= 0
          text: "End of chapter"
          bordered: true
          foreground: root.fg
          fontFamily: root.fontFamily
          onClicked: root.sleepUntilChapterEnd()
        }
      }
      RowLayout {
        visible: root.configured && !root.settingsView && root.sleepView
        Layout.fillWidth: true
        spacing: Style.spacing.md
        TextField {
          id: sleepMinutes
          Layout.fillWidth: true
          placeholderText: "Custom minutes"
          foreground: root.fg
          font.family: root.fontFamily
          validator: IntValidator { bottom: 1; top: 720 }
          onAccepted: { root.startSleep(parseInt(text)); text = "" }
        }
        Button {
          text: "Start"
          bordered: true
          foreground: root.fg
          fontFamily: root.fontFamily
          enabled: sleepMinutes.text !== "" && parseInt(sleepMinutes.text) > 0
          onClicked: { root.startSleep(parseInt(sleepMinutes.text)); sleepMinutes.text = "" }
        }
      }
      Button {
        visible: root.configured && !root.settingsView && root.sleepView && root.sleeping
        text: "Cancel timer"
        iconText: "󰅖"
        bordered: true
        foreground: root.fg
        fontFamily: root.fontFamily
        onClicked: root.cancelSleep()
      }

      // ---- speakers / groups ------------------------------------------------
      PanelSectionHeader {
        visible: root.configured && !root.settingsView && root.speakersView
        text: "SPEAKERS" + (root.speakersLeaderName !== "" ? "  ·  " + root.speakersLeaderName.toUpperCase() + (root.memberCount > 1 ? " +" + (root.memberCount - 1) : "") : "")
        foreground: root.fg
        fontFamily: root.fontFamily
      }
      Text {
        textFormat: Text.PlainText
        visible: root.configured && !root.settingsView && root.speakersView
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: root.speakersError !== "" ? root.speakersError
          : (root.speakersCanGroup ? "Tick speakers to play on them together with " + root.speakersLeaderName + "."
             : "This player can't be grouped. Pick another player from the list at the top.")
        color: root.speakersError !== "" ? Color.accent : root.mutedFg
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
      PanelSectionHeader {
        visible: root.configured && !root.settingsView && root.speakersView && root.groupPlayers.length > 0
        text: "SAVED GROUPS"
        foreground: root.fg
        fontFamily: root.fontFamily
      }
      Flow {
        visible: root.configured && !root.settingsView && root.speakersView && root.groupPlayers.length > 0
        Layout.fillWidth: true
        spacing: Style.spacing.md
        Repeater {
          model: root.groupPlayers
          delegate: Row {
            id: gp
            required property var modelData
            spacing: 0
            Button {
              text: gp.modelData.name
              iconText: "󰓃"
              bordered: true
              selected: gp.modelData.id === root.playerId
              foreground: root.fg
              fontFamily: root.fontFamily
              tooltipText: "Control and play on this group"
              onClicked: root.pickPlayer(gp.modelData.id)
            }
            PanelActionButton {
              iconText: root.confirmDeleteId === gp.modelData.id ? "󰗨" : "󰅖"
              tooltipText: root.confirmDeleteId === gp.modelData.id ? "Click again to delete this group" : "Delete this group"
              foreground: root.confirmDeleteId === gp.modelData.id ? Color.accent : root.fg
              onClicked: root.deleteGroup(gp.modelData.id)
            }
          }
        }
      }
      RowLayout {
        visible: root.configured && !root.settingsView && root.speakersView && root.memberCount > 1
        Layout.fillWidth: true
        spacing: Style.spacing.md
        TextField {
          id: groupName
          visible: !root.speakersIsGroup
          Layout.fillWidth: true
          placeholderText: "Save these speakers as a group…"
          foreground: root.fg
          font.family: root.fontFamily
          onAccepted: root.saveGroup()
        }
        Item { visible: root.speakersIsGroup; Layout.fillWidth: true }
        Button {
          visible: !root.speakersIsGroup
          text: "Save"
          bordered: true
          foreground: root.fg
          fontFamily: root.fontFamily
          onClicked: root.saveGroup()
        }
        Button {
          visible: !root.speakersIsGroup
          text: "Ungroup"
          bordered: true
          foreground: root.fg
          fontFamily: root.fontFamily
          onClicked: root.ungroupAll()
        }
      }
      RowLayout {
        visible: root.configured && !root.settingsView && root.speakersView && root.memberCount > 1
          && !root.speakersIsGroup && root.mixedGroupsAvailable
        Layout.fillWidth: true
        spacing: Style.spacing.md
        Text {
          textFormat: Text.PlainText
          text: "Group type:"
          color: root.mutedFg
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
        ButtonGroup {
          options: [
            { value: "sync_group", label: "In sync", tooltip: "Same-brand speakers, kept in sync" },
            { value: "universal_group", label: "Mixed brands", tooltip: "Different brands play the same audio, but not in sync" }
          ]
          value: root.groupKind
          foreground: root.fg
          fontFamily: root.fontFamily
          focusable: false
          onChanged: function(v) { root.groupKindChoice = v }
        }
        Item { Layout.fillWidth: true }
      }
      ListView {
        id: speakerList
        visible: root.configured && !root.settingsView && root.speakersView
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: Style.spacing.xs
        boundsBehavior: Flickable.StopAtBounds
        model: root.speakers
        delegate: Rectangle {
          id: sp
          required property var modelData
          width: speakerList.width
          height: Style.space(48)
          radius: Style.cornerRadius
          color: spMouse.containsMouse ? Style.hoverFillFor(root.fg, Color.accent) : "transparent"
          MouseArea {
            id: spMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: sp.modelData.leader || !root.speakersCanGroup ? Qt.ArrowCursor : Qt.PointingHandCursor
            onClicked: root.toggleSpeaker(sp.modelData)
          }
          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(12)
            anchors.rightMargin: Style.space(6)
            spacing: Style.spacing.lg
            Text {
              textFormat: Text.PlainText
              text: sp.modelData.member ? "󰄲" : "󰄱"
              color: sp.modelData.member ? Color.accent : root.mutedFg
              font.family: root.fontFamily
              font.pixelSize: Style.font.icon
            }
            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.spacing.xxs
              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: sp.modelData.name
                color: root.fg
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: (sp.modelData.leader ? "Leader  ·  " : "") + sp.modelData.provider.split("--")[0]
                  + (sp.modelData.state === "playing" ? "  ·  playing" : "")
                color: root.mutedFg
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }
            PanelSlider {
              visible: sp.modelData.member && sp.modelData.volume !== null && sp.modelData.volume !== undefined
              Layout.preferredWidth: Style.space(110)
              bar: root.bar
              minimum: 0
              maximum: 100
              step: 1
              integer: true
              value: sp.modelData.volume || 0
              onReleased: function(v) { root.setSpeakerVolume(sp.modelData, v) }
            }
            Text {
              visible: sp.modelData.member && sp.modelData.volume !== null && sp.modelData.volume !== undefined
              Layout.preferredWidth: Style.space(30)
              horizontalAlignment: Text.AlignRight
              textFormat: Text.PlainText
              text: Math.round(sp.modelData.volume || 0)
              color: root.mutedFg
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
        }
      }

      // Without a visible list to fill the leftover height, a ColumnLayout
      // spreads the sections apart; this keeps them pinned to the top.
      Item {
        Layout.fillHeight: true
        visible: !list.visible && !queueList.visible && !chapterList.visible && !bookmarkList.visible && !speakerList.visible
      }
    }

    // ---- right-click menu ---------------------------------------------------
    // Click anywhere else to dismiss it.
    MouseArea {
      anchors.fill: parent
      visible: root.menuItem !== null
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      onClicked: root.menuItem = null
    }

    Rectangle {
      id: menuCard
      visible: root.menuItem !== null
      x: Math.max(0, Math.min(root.menuX, keyCatcher.width - width))
      y: Math.max(0, Math.min(root.menuY, keyCatcher.height - height))
      width: Style.space(220)
      height: menuColumn.implicitHeight + Style.space(12)
      radius: Style.cornerRadius
      color: Color.popups.background
      border.color: Color.popups.border
      border.width: Math.max(1, Style.space(1))

      Column {
        id: menuColumn
        anchors.fill: parent
        anchors.margins: Style.space(6)
        spacing: Style.spacing.xxs
        Button {
          width: parent.width
          leftAlign: true
          iconText: "󰐊"
          text: "Play now"
          foreground: Color.popups.text
          fontFamily: root.fontFamily
          onClicked: root.enqueue(root.menuItem, "play")
        }
        Button {
          width: parent.width
          leftAlign: true
          iconText: "󰒭"
          text: "Play next"
          foreground: Color.popups.text
          fontFamily: root.fontFamily
          onClicked: root.enqueue(root.menuItem, "next")
        }
        Button {
          width: parent.width
          leftAlign: true
          iconText: "󰐕"
          text: "Add to queue"
          foreground: Color.popups.text
          fontFamily: root.fontFamily
          onClicked: root.enqueue(root.menuItem, "add")
        }
      }
    }
  }
}
