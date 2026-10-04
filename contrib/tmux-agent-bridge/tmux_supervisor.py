"""Keep one tmux session per mapped Lark group running its agent.

Run periodically by launchd (ctl.sh install → <prefix>.tmux). Each run:
  - creates `lark-<群名>` for groups that have no session yet
  - respawns the pane in place when its agent has exited (remain-on-exit keeps it)
Restarts resume the tmux-hosted session recorded by notify.py (tmux_sessions.json).

Usage: tmux_supervisor.py [ensure|start|stop|status]
  ensure  one pass (what launchd runs)          start  clear pause flag, then ensure
  stop    pause auto-start and kill lark-* sessions   status  show sessions
"""
import logging
import shlex
import subprocess
import sys
from dataclasses import dataclass

from config import (DIR_MAP_PATH, HOME, LOG_PATH, TMUX_SESSIONS_PATH, load_json,
                    normalize_dir)
from tmux_inject import TMUX_TIMEOUT_SEC, TmuxError, tmux_bin

SESSION_PREFIX = "lark-"
PAUSE_FLAG = HOME / "tmux.paused"
LOGIN_SHELL = "/bin/zsh"
PANE_SIZE = ("220", "50")          # detached sessions need an explicit size
CODEX_BASE = "codex -c check_for_update_on_startup=false"

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s tmux %(levelname)s %(message)s")
log = logging.getLogger("tmux")


@dataclass(frozen=True)
class Action:
    kind: str        # "create" | "respawn"
    name: str
    directory: str
    command: str


def session_name(group: str) -> str:
    """tmux treats '.' and ':' as target separators."""
    return SESSION_PREFIX + group.replace(".", "_").replace(":", "_")


def agent_command(agent: str, session_id: str) -> str:
    """Resume the recorded session when there is one, else start fresh."""
    fresh = "claude" if agent == "claude" else CODEX_BASE
    if not session_id:
        return fresh
    sid = shlex.quote(session_id)
    resume = f"claude --resume {sid}" if agent == "claude" else f"{CODEX_BASE} resume {sid}"
    return f"{resume} || {fresh}"


def shell_wrap(command: str) -> str:
    """Login + interactive zsh so the agent sees the same env as a Terminal tab."""
    return f"{LOGIN_SHELL} -lic {shlex.quote(command)}"


def plan(dir_map: dict, live: dict, tmux_sessions: dict) -> list:
    """live: {session name: pane_dead}. Returns the actions needed, in dir_map order."""
    actions = []
    for entry in dir_map.values():
        name = session_name(entry["name"])
        if name in live and not live[name]:
            continue
        sid = tmux_sessions.get(normalize_dir(entry["dir"]), "")
        kind = "respawn" if name in live else "create"
        actions.append(Action(kind, name, entry["dir"],
                              shell_wrap(agent_command(entry["agent"], sid))))
    return actions


def _tmux(args: list) -> str:
    proc = subprocess.run([tmux_bin(), *args], capture_output=True, text=True,
                          timeout=TMUX_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise TmuxError(proc.stderr.strip() or f"tmux {args[0]} 失败")
    return proc.stdout


def live_sessions() -> dict:
    """{lark-* session name: pane_dead} for the first pane of each session."""
    try:
        out = _tmux(["list-panes", "-a", "-F", "#{session_name}\t#{pane_index}\t#{pane_dead}"])
    except TmuxError:
        return {}  # no server running yet
    live = {}
    for line in out.splitlines():
        name, index, dead = (line.split("\t") + ["", ""])[:3]
        if name.startswith(SESSION_PREFIX) and index == "0":
            live[name] = dead == "1"
    return live


def _apply(action: Action) -> None:
    if action.kind == "create":
        _tmux(["new-session", "-d", "-s", action.name, "-c", action.directory,
               "-x", PANE_SIZE[0], "-y", PANE_SIZE[1], action.command])
    else:
        _tmux(["respawn-pane", "-k", "-t", f"{action.name}:0.0", "-c", action.directory,
               action.command])
    log.info("%s %s in %s", action.kind, action.name, action.directory)


def ensure(dir_map: dict = None) -> list:
    if PAUSE_FLAG.exists():
        return []
    dir_map = load_json(DIR_MAP_PATH) if dir_map is None else dir_map
    live = live_sessions()
    actions = plan(dir_map, live, load_json(TMUX_SESSIONS_PATH))
    for action in actions:
        try:
            _apply(action)
        except (TmuxError, OSError, subprocess.TimeoutExpired) as exc:
            log.error("%s %s failed: %s", action.kind, action.name, exc)
    # Keep exited panes around so the next pass can respawn them in place.
    for name in {*live, *(a.name for a in actions)}:
        try:
            _tmux(["set-option", "-t", name, "remain-on-exit", "on"])
        except TmuxError as exc:
            log.error("remain-on-exit %s failed: %s", name, exc)
    return actions


def stop() -> None:
    PAUSE_FLAG.write_text("paused\n", encoding="utf-8")
    for name in live_sessions():
        _tmux(["kill-session", "-t", name])
        log.info("killed %s", name)


def status() -> str:
    live = live_sessions()
    lines = [f"自动拉起：{'已暂停' if PAUSE_FLAG.exists() else '开启'}"]
    lines += [f"  {name}: {'已退出，等待重启' if dead else '运行中'}" for name, dead in live.items()]
    return "\n".join(lines) if live else lines[0] + "\n  (没有 lark-* 会话)"


def main(argv: list) -> None:
    cmd = argv[0] if argv else "ensure"
    if cmd == "start":
        PAUSE_FLAG.unlink(missing_ok=True)
        cmd = "ensure"
    if cmd == "ensure":
        for action in ensure():
            print(f"{action.kind}: {action.name}")
    elif cmd == "stop":
        stop()
        print("已暂停自动拉起，并关闭了 lark-* 会话")
    elif cmd == "status":
        print(status())
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
