import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar entry point: a music-note glyph that reflects what the selected Music
// Assistant player is doing, hosting the dropdown in Panel.qml. Left click
// opens the dropdown, middle click plays / pauses, right click skips to the
// next item.
BarWidget {
  id: root
  moduleName: "music-assistant"

  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")

  property bool configured: true
  property bool reachable: true
  property string state: "idle"
  property string title: ""
  property string subtitle: ""
  readonly property bool playing: state === "playing"

  readonly property string tooltipText: {
    if (!root.configured) return "Music Assistant: not connected (see README)"
    if (!root.reachable) return "Music Assistant: server unreachable"
    if (root.title === "") return "Music Assistant"
    var line = (root.playing ? "Playing: " : "Paused: ") + root.title
    return root.subtitle !== "" ? line + " — " + root.subtitle : line
  }

  function backend(args) {
    return ["python3", root.pluginDir + "/scripts/ma_backend.py"].concat(args)
  }

  function control(action) {
    ctlProcess.command = backend(["ctl", action])
    ctlProcess.running = true
    // The server reports the new state about a second or two after a command.
    refreshSoon.restart()
  }

  function refresh() {
    if (!nowProcess.running) {
      nowProcess.command = root.backend(["now"])
      nowProcess.running = true
    }
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  // Shape contract for shell.summon/hide/toggle routing (Bar.findPanelWidget).
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "music-assistant"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
    function playPause(): void { root.control("playpause") }
    function next(): void { root.control("next") }
    function previous(): void { root.control("previous") }
  }

  Timer {
    interval: Math.max(2, root.setting("pollSeconds", 3)) * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    id: refreshSoon
    interval: 1500
    repeat: false
    onTriggered: root.refresh()
  }

  Process {
    id: nowProcess
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          if (parsed.error) {
            root.configured = parsed.error !== "not connected yet"
            root.reachable = root.configured ? false : true
            return
          }
          root.configured = true
          root.reachable = true
          root.state = parsed.state || "idle"
          root.title = parsed.item ? parsed.item.name : ""
          root.subtitle = parsed.item ? parsed.item.subtitle : ""
        } catch (e) {
          root.reachable = false
        }
      }
    }
  }

  // Fire-and-forget; the next poll picks up the new state.
  Process { id: ctlProcess }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.playing ? "󰎈" : "󰎆"
    tooltipText: root.tooltipText
    active: root.playing
    dimmed: !root.configured || !root.reachable
    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.control("playpause")
      else if (b === Qt.RightButton) root.control("next")
      else root.togglePanel()
    }
  }
}
