"""Attach to a group's tmux-hosted agent, starting it first if needed.

Usage:
  tmux_open.py <群名>          e.g. tmux_open.py 日语音频群
  tmux_open.py --list          show group names
Detach with Ctrl-b d; the agent keeps running and tmux_supervisor.py restarts it if it exits.
"""
import os
import sys

from config import DIR_MAP_PATH, load_json
from tmux_inject import tmux_bin
from tmux_supervisor import ensure, session_name


def main(argv: list) -> None:
    entries = {e["name"]: e for e in load_json(DIR_MAP_PATH).values()}
    if len(argv) != 1 or argv[0] == "--list":
        print("可用的群：", "、".join(entries))
        raise SystemExit(0 if argv[:1] == ["--list"] else 2)
    group = argv[0]
    if group not in entries:
        raise SystemExit(f"未知或未接通的群：{group}（可用：{'、'.join(entries)}）")
    ensure({"one": entries[group]})
    tmux = tmux_bin()
    os.execv(tmux, [tmux, "attach-session", "-t", session_name(group)])


if __name__ == "__main__":
    main(sys.argv[1:])
