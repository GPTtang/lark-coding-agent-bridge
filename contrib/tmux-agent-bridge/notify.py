"""Hook handler for Claude Code and Codex.

Usage (stdin = hook JSON):
  notify.py claude start|stop|notification
  notify.py codex  stop
Records the latest session id per directory and posts to the mapped Feishu group.
Never fails the agent: all errors are logged and exit code is 0.
"""
import json
import logging
import os
import sys

from config import (BRIDGE_ENV_FLAG, DIR_MAP_PATH, LOG_PATH, SESSIONS_PATH,
                    TMUX_SESSIONS_PATH, chat_for_dir, load_json, normalize_dir, save_json)

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s notify %(levelname)s %(message)s")

SESSION_KEYS = ("session_id", "thread_id", "conversation_id")
PREVIEW_CHARS = 1500


def _session_id(payload: dict) -> str:
    return next((str(payload[k]) for k in SESSION_KEYS if payload.get(k)), "")


def _texts_from_entry(entry: dict) -> list:
    """Assistant text from one transcript line (Claude or Codex rollout format)."""
    msg = entry.get("message") or entry.get("payload") or {}
    is_assistant = entry.get("type") == "assistant" or msg.get("role") == "assistant"
    if not is_assistant:
        return []
    content = msg.get("content", [])
    if isinstance(content, str):
        return [content]
    return [c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") in ("text", "output_text")]


def last_assistant_text(payload: dict) -> str:
    for key in ("last_assistant_message", "last-assistant-message"):
        if payload.get(key):
            return str(payload[key])
    path = payload.get("transcript_path")
    if not path or not os.path.exists(path):
        return ""
    last = ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                texts = _texts_from_entry(json.loads(line))
            except json.JSONDecodeError:
                continue
            if any(t.strip() for t in texts):
                last = "\n".join(texts)
    return last


def build_message(agent: str, event: str, name: str, payload: dict) -> str:
    if event == "start":
        return f"🟢 [{agent}] {name} 新会话已开始。在群里直接发消息即可继续该会话。"
    if event == "notification":
        return f"🔔 [{agent}] {name}：{payload.get('message', '需要你处理')}"
    text = last_assistant_text(payload)
    if len(text) > PREVIEW_CHARS:
        text = text[:PREVIEW_CHARS] + "\n…（已截断）"
    return f"✅ [{agent}] {name} 本轮完成\n\n{text or '(无文本输出)'}"


def record_session(cwd: str, agent: str, session_id: str) -> None:
    if not session_id:
        return
    sessions = load_json(SESSIONS_PATH)
    save_json(SESSIONS_PATH, {**sessions, normalize_dir(cwd): {"agent": agent,
                                                                "session_id": session_id}})


def record_tmux_session(cwd: str, session_id: str) -> None:
    """Remember which session the tmux-hosted agent runs, so a restart resumes it."""
    if not session_id or not os.environ.get("TMUX_PANE"):
        return
    sessions = load_json(TMUX_SESSIONS_PATH)
    save_json(TMUX_SESSIONS_PATH, {**sessions, normalize_dir(cwd): session_id})


def handle(agent: str, event: str, payload: dict) -> None:
    cwd = payload.get("cwd") or os.getcwd()
    match = chat_for_dir(load_json(DIR_MAP_PATH), cwd)
    if not match:
        return
    chat_id, entry = match
    record_session(entry["dir"], agent, _session_id(payload))
    record_tmux_session(entry["dir"], _session_id(payload))
    if os.environ.get(BRIDGE_ENV_FLAG):
        return  # the bridge itself posts results of turns it started
    from feishu_api import send_text  # lazy: avoid requests import when unmapped
    send_text(chat_id, build_message(agent, event, entry["name"], payload))


def main() -> None:
    agent = sys.argv[1] if len(sys.argv) > 1 else "claude"
    event = sys.argv[2] if len(sys.argv) > 2 else "stop"
    try:
        raw = sys.stdin.read()
        handle(agent, event, json.loads(raw) if raw.strip() else {})
    except (Exception, SystemExit):  # noqa: BLE001 - a hook must never break the agent
        logging.exception("hook failed agent=%s event=%s", agent, event)
    sys.exit(0)


if __name__ == "__main__":
    main()
