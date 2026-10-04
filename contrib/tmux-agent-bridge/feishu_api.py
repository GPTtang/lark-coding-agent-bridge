"""Minimal Feishu Open API client (tenant token, list chats, send text)."""
import json
import os
import time

import requests

from config import FEISHU_BASE, HTTP_TIMEOUT, chunk_text, require_env

_token_cache = {"value": "", "expires_at": 0.0}


class FeishuError(RuntimeError):
    pass


def _check(resp: requests.Response) -> dict:
    resp.raise_for_status()
    body = resp.json()
    if body.get("code", 0) != 0:
        raise FeishuError(f"飞书 API 错误 code={body.get('code')} msg={body.get('msg')}")
    return body


def tenant_token() -> str:
    if _token_cache["value"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["value"]
    body = _check(requests.post(
        f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": require_env("FEISHU_APP_ID"),
              "app_secret": require_env("FEISHU_APP_SECRET")},
        timeout=HTTP_TIMEOUT,
    ))
    _token_cache.update(value=body["tenant_access_token"],
                        expires_at=time.time() + body.get("expire", 7200) - 300)
    return _token_cache["value"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {tenant_token()}"}


def list_bot_chats() -> list:
    """All groups the bot is a member of: [{chat_id, name, owner_id}, ...]."""
    chats, page_token = [], ""
    while True:
        params = {"page_size": 100, "user_id_type": "open_id"}
        if page_token:
            params["page_token"] = page_token
        data = _check(requests.get(f"{FEISHU_BASE}/im/v1/chats", headers=_headers(),
                                   params=params, timeout=HTTP_TIMEOUT))["data"]
        chats.extend(data.get("items", []))
        if not data.get("has_more"):
            return chats
        page_token = data["page_token"]


def send_text(chat_id: str, text: str) -> None:
    for part in chunk_text(text):
        _check(requests.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=_headers(),
            json={"receive_id": chat_id, "msg_type": "text",
                  "content": json.dumps({"text": part}, ensure_ascii=False)},
            timeout=HTTP_TIMEOUT,
        ))


IMAGE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
             "image/webp": ".webp"}


def download_image(message_id: str, image_key: str, dest_dir: str) -> str:
    """Save an image from a received message; returns the local file path."""
    resp = requests.get(f"{FEISHU_BASE}/im/v1/messages/{message_id}/resources/{image_key}",
                        params={"type": "image"}, headers=_headers(), timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    if resp.headers.get("Content-Type", "").startswith("application/json"):
        _check(resp)  # API errors come back as JSON instead of bytes
    ext = IMAGE_EXT.get(resp.headers.get("Content-Type", "").split(";")[0], ".png")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{image_key}{ext}")
    with open(path, "wb") as f:
        f.write(resp.content)
    return path
