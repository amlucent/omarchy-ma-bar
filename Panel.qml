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
    if (root.settingsView && root.configured) { root.settingsView = false; return true }
    if (root.openPodcast) { root.openPodcast = null; root.loadList(); return true }
    if (root.query !== "") { searchField.text = ""; return true }
    return false
  }

  function refreshAll() {
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
  onQueryChanged: searchDebounce.restart()

  Timer { id: searchDebounce; interval: 350; onTriggered: { root.openPodcast = null; root.loadList() } }
  // The server reports new state a second or two after a command.
  Timer { id: refreshSoon; interval: 1500; onTriggered: root.pollNow() }
  Timer {
    interval: 2000
    running: root.opened && root.configured
    repeat: true
    onTriggered: root.pollNow()
  }
  // Smooth the progress bar between server updates.
  Timer {
    interval: 1000
    running: root.opened && root.playing
    repeat: true
    onTriggered: if (root.duration <= 0 || root.position < root.duration) root.position += root.speed
  }

  Component.onCompleted: statusProc.running = true

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
          if (r.error) return
          root.state = r.state || "idle"
          root.playerId = r.id || root.playerId
          root.playerName = r.player || root.playerName
          root.volume = r.volume === undefined || r.volume === null ? root.volume : r.volume
          root.speed = r.speed || 1.0
          if (r.item) {
            root.title = r.item.name || ""
            root.subtitle = r.item.subtitle || ""
            root.itemType = r.item.type || ""
            root.cover = r.item.cover || ""
            root.duration = r.item.duration || 0
          } else {
            root.title = ""; root.subtitle = ""; root.itemType = ""; root.cover = ""; root.duration = 0
          }
          root.position = r.elapsed || 0
        } catch (e) {}
      }
    }
  }

  Process { id: ctlProc }
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
          root.players = list.map(function(p) { return { value: p.id, label: p.name } })
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
              text: root.hasItem ? root.title : "Nothing playing"
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
            }
            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              text: root.hasItem ? root.subtitle : (root.playerName !== "" ? "on " + root.playerName : "")
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
        visible: root.configured && !root.settingsView && root.openPodcast === null
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
        visible: root.configured && !root.settingsView && root.openPodcast === null
        Layout.fillWidth: true
        placeholderText: "Search music, audiobooks and podcasts  ( / )"
        foreground: root.fg
        font.family: root.fontFamily
        onTextChanged: root.query = text.trim()
      }

      Text {
        textFormat: Text.PlainText
        visible: root.configured && !root.settingsView && (root.listError !== "" || (!root.loading && root.rows.length === 0))
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
        visible: root.configured && !root.settingsView
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
                    if (m.duration) parts.push(root.formatTime(m.duration))
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

            MouseArea {
              id: rowMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.activate(del.modelData)
            }
          }
        }
      }
    }
  }
}
