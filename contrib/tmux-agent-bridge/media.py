"""Image support: parse Feishu message content and hold images until the next text.

Feishu clients often send a screenshot and the question as two separate messages,
so an image-only message is parked per chat and attached to the next text message.
"""
import json
import os
import re
import threading
import time

from config import HOME

INBOX_DIR = HOME / "inbox"        # downloaded images, one subdir per chat
PENDING_TTL_SEC = 30 * 60         # parked images older than this are dropped
INBOX_MAX_AGE_SEC = 24 * 60 * 60  # injected turns read images later; prune after a day
MENTION_RE = re.compile(r"@_user_\d+\s*")

_pending = {}                     # chat_id -> (timestamp, [paths])
_lock = threading.Lock()


def _post_body(content: dict) -> dict:
    """Post content is either {title, content} or localized {zh_cn: {...}}."""
    if "content" in content:
        return content
    return next((v for v in content.values() if isinstance(v, dict)), {})


def _parse_post(content: dict) -> tuple:
    body = _post_body(content)
    lines = [body.get("title", "")]
    keys = []
    for paragraph in body.get("content", []):
        texts = []
        for node in paragraph:
            if node.get("tag") in ("text", "a"):
                texts.append(node.get("text", ""))
            elif node.get("tag") == "img" and node.get("image_key"):
                keys.append(node["image_key"])
        lines.append("".join(texts))
    text = "\n".join(line.strip() for line in lines if line.strip())
    return text, keys


def parse_content(message_type: str, raw: str) -> tuple:
    """Return (text, image_keys) for text / image / post messages; others -> ("", [])."""
    try:
        content = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "", []
    if message_type == "text":
        return MENTION_RE.sub("", content.get("text", "")).strip(), []
    if message_type == "image":
        key = content.get("image_key")
        return "", [key] if key else []
    if message_type == "post":
        text, keys = _parse_post(content)
        return MENTION_RE.sub("", text).strip(), keys
    return "", []


def add_pending(chat_id: str, paths: list) -> int:
    """Park images for this chat; returns how many are now waiting."""
    with _lock:
        _, existing = _pending.get(chat_id, (0, []))
        merged = [*existing, *paths]
        _pending[chat_id] = (time.time(), merged)
        return len(merged)


def take_pending(chat_id: str) -> list:
    with _lock:
        stamp, paths = _pending.pop(chat_id, (0, []))
    if time.time() - stamp > PENDING_TTL_SEC:
        cleanup(paths)
        return []
    return paths


def cleanup(paths: list) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def prune_inbox() -> None:
    """Delete downloaded images older than INBOX_MAX_AGE_SEC."""
    cutoff = time.time() - INBOX_MAX_AGE_SEC
    if not INBOX_DIR.exists():
        return
    for path in INBOX_DIR.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            cleanup([str(path)])
