"""Talk to Muxy (https://github.com/muxy-app/muxy) through its bundled CLI.

Muxy's socket protocol is one line per command, so text sent to a pane cannot
contain newlines: they are flattened to spaces and Enter is sent separately.
"""
import os
import re
import subprocess
import time

MUXY_CLI = os.environ.get(
    "MUXY_CLI", "/Applications/Muxy.app/Contents/Resources/Muxy_Muxy.bundle/scripts/muxy-cli")
MUXY_SOCKET = os.environ.get(
    "MUXY_SOCKET_PATH", os.path.expanduser("~/Library/Application Support/Muxy/muxy.sock"))
CLI_TIMEOUT_SEC = 15
SCREEN_LINES = 25
SUBMIT_SETTLE_SEC = 0.3


class MuxyError(RuntimeError):
    pass


def available() -> bool:
    return os.path.exists(MUXY_CLI) and os.path.exists(MUXY_SOCKET)


def _cli(*args: str) -> str:
    proc = subprocess.run(["/bin/bash", MUXY_CLI, *args], capture_output=True, text=True,
                          timeout=CLI_TIMEOUT_SEC, env={**os.environ, "MUXY_SOCKET_PATH": MUXY_SOCKET})
    if proc.returncode != 0:
        raise MuxyError((proc.stderr or proc.stdout).strip() or f"muxy {args[0]} 失败")
    return proc.stdout


def parse_panes(output: str) -> list:
    """`muxy list-panes` -> [(pane id, title, cwd)]."""
    panes = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] and parts[2]:
            panes.append((parts[0], parts[1], parts[2]))
    return panes


def list_panes() -> list:
    if not available():
        return []
    try:
        return parse_panes(_cli("list-panes"))
    except (MuxyError, OSError, subprocess.TimeoutExpired):
        return []  # Muxy not running


def read_screen(pane_id: str) -> str:
    return _cli("read-screen", "--pane", pane_id, "--lines", str(SCREEN_LINES))


def send(pane_id: str, text: str) -> None:
    flat = re.sub(r"\s*\n\s*", " ", text).strip()
    _cli("send", "--pane", pane_id, flat)
    time.sleep(SUBMIT_SETTLE_SEC)
    _cli("send-keys", "--pane", pane_id, "Enter")
