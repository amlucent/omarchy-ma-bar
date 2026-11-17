#!/usr/bin/env python3
"""Run a Sendspin player on this computer so Music Assistant can play through its speakers.

EXPERIMENTAL helper for the README's "Play on this computer without the Companion app".
Run it with the Python from the `sendspin` uv tool, not the system Python:

    ~/.local/share/uv/tools/sendspin/bin/python tools/sendspin-pipewire.py ws://YOUR-SERVER:8927/sendspin

The server address can also come from SENDSPIN_URL, the player name from SENDSPIN_NAME
(default: this computer's short hostname, which the plugin looks for automatically).

Why a wrapper: on PipeWire, PortAudio can hang while the Sendspin client tests which audio
formats the output supports. PipeWire converts formats itself, so this skips that test.
It relies on Sendspin internals and may need updating when Sendspin changes.
"""

import os
import socket
import sys


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SENDSPIN_URL", "")
    if not url.startswith(("ws://", "wss://")):
        sys.exit("usage: sendspin-pipewire.py ws://YOUR-SERVER:8927/sendspin")
    name = os.environ.get("SENDSPIN_NAME") or socket.gethostname().split(".")[0]

    import sounddevice
    sounddevice.check_output_settings = lambda *args, **kwargs: None

    sys.argv = ["sendspin", "daemon", "--url", url, "--audio-device", "pipewire", "--name", name,
                "--hardware-volume", "false", "--disable-mpris"]
    from sendspin.cli import main as run
    run()


if __name__ == "__main__":
    main()
