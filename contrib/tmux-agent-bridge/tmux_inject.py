"""Inject group messages into a Claude Code / Codex session running in tmux.

When the mapped directory has a tmux pane running the right agent, the bridge
types the message into that pane (bracketed paste + Enter) so it shows up live
on screen, instead of running a separate headless process. The pane's own Stop
hook (notify.py) then posts the reply back to the group.
"""
import os
import re
import shutil
import subprocess
import time

from config import normalize_dir

BUFFER = "feishu-bridge"
PASTE_SETTLE_SEC = 0.3          # let the TUI finish handling the paste before Enter
TMUX_TIMEOUT_SEC = 10
TMUX_FALLBACKS = ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux")

# argv[0] patterns; Claude's native binary is named after its version number.
_AGENT_PATTERNS = {
    "claude": re.compile(r"(^|/)claude(\s|$)|/claude/versions/[^/\s]+(\s|$)"),
    "codex": re.compile(r"(^|/)codex(\s|$)|node\s+\S*/codex(\.js)?(\s|$)"),
}


class TmuxError(RuntimeError):
    pass


def tmux_bin() -> str:
    found = shutil.which("tmux") or next((p for p in TMUX_FALLBACKS if os.path.exists(p)), "")
    if not found:
        raise TmuxError("没有找到 tmux")
    return found


def _tmux(args: list, stdin: str = None) -> str:
    proc = subprocess.run([tmux_bin(), *args], input=stdin, capture_output=True,
                          text=True, timeout=TMUX_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise TmuxError(proc.stderr.strip() or f"tmux {args[0]} 失败")
    return proc.stdout


def parse_panes(output: str) -> list:
    """`list-panes -F '#{pane_id}\\t#{pane_current_path}\\t#{pane_pid}'` -> [(id, path, pid)]."""
    panes = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2].isdigit():
            panes.append((parts[0], parts[1], int(parts[2])))
    return panes


def parse_ps(output: str) -> dict:
    """`ps -A -o pid=,ppid=,command=` -> {pid: (ppid, command)}."""
    procs = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            procs[int(parts[0])] = (int(parts[1]), parts[2])
    return procs


def list_panes() -> list:
    try:
        return parse_panes(_tmux(["list-panes", "-a", "-F",
                                  "#{pane_id}\t#{pane_current_path}\t#{pane_pid}"]))
    except (TmuxError, OSError, subprocess.TimeoutExpired):
        return []  # no tmux server / not installed -> no panes


def process_table() -> dict:
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True,
                         text=True, timeout=TMUX_TIMEOUT_SEC).stdout
    return parse_ps(out)


def is_agent_command(agent: str, command: str) -> bool:
    pattern = _AGENT_PATTERNS.get(agent)
    return bool(pattern and pattern.search(command.split(" -", 1)[0] + " "))


def _descendants(root: int, procs: dict) -> list:
    children = {}
    for pid, (ppid, _) in procs.items():
        children.setdefault(ppid, []).append(pid)
    found, stack = [], [root]
    while stack:
        pid = stack.pop()
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


def find_pane(directory: str, agent: str, panes: list = None, procs: dict = None):
    """Pane id whose cwd is `directory` and whose process tree runs `agent`, else None."""
    target = normalize_dir(directory)
    panes = list_panes() if panes is None else panes
    candidates = [(pane_id, pid) for pane_id, path, pid in panes if normalize_dir(path) == target]
    if not candidates:
        return None
    procs = process_table() if procs is None else procs
    for pane_id, root in candidates:
        if any(is_agent_command(agent, procs[p][1])
               for p in _descendants(root, procs) if p in procs):
            return pane_id
    return None


def inject(pane_id: str, text: str) -> None:
    """Paste text as one bracketed paste (multi-line safe), then submit with Enter."""
    _tmux(["load-buffer", "-b", BUFFER, "-"], stdin=text)
    _tmux(["paste-buffer", "-p", "-d", "-b", BUFFER, "-t", pane_id])
    time.sleep(PASTE_SETTLE_SEC)
    _tmux(["send-keys", "-t", pane_id, "Enter"])

