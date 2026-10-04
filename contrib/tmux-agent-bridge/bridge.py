"""Long-running Feishu listener: group message -> agent turn in mapped dir -> reply.

Group commands:
  /status        show mapped dir, agent, session and where messages go
  /new <prompt>  start a fresh headless session instead of resuming
  //<cmd>        send /<cmd> to the CLI in the terminal pane, then post the screen
  /screen        post the terminal pane's current screen
  /esc           press Escape in the terminal pane (close a panel / dialog)
  anything else  typed into the terminal pane, or a headless turn when there is none
"""
import logging
import os
import threading
from collections import OrderedDict

import lark_oapi as lark

from config import DIR_MAP_PATH, FEISHU_DOMAIN, LOG_PATH, load_json, require_env
import media
from feishu_api import download_image, send_text
from runner import current_session, run_turn, with_image_note
import time

from injector import find_target, inject, press_key, screen_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s bridge %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
log = logging.getLogger("bridge")

SEEN_LIMIT = 500
SCREEN_SETTLE_SEC = 2.0          # let the CLI draw its panel before we capture the screen
TERMINAL_COMMANDS = ("/screen", "/esc")
_seen = OrderedDict()            # message_id dedupe (Feishu may redeliver)
_chat_locks = {}                 # chat_id -> Lock, one agent turn per group at a time
_state_lock = threading.Lock()


def allowed_senders(entry: dict) -> set:
    extra = os.environ.get("FEISHU_ALLOWED_OPEN_IDS", "")
    return ({entry.get("owner_open_id", "")} | {x.strip() for x in extra.split(",")}) - {""}


def first_time(message_id: str) -> bool:
    with _state_lock:
        if message_id in _seen:
            return False
        _seen[message_id] = True
        while len(_seen) > SEEN_LIMIT:
            _seen.popitem(last=False)
        return True


def chat_lock(chat_id: str) -> threading.Lock:
    with _state_lock:
        return _chat_locks.setdefault(chat_id, threading.Lock())


def _status(directory: str, agent: str) -> str:
    sid = current_session(directory) or "(无记录，将使用该目录最近的会话)"
    found = find_target(directory, agent)
    if found.target and found.blocked:
        mode = f"⚠️ {found.target.label} 正被弹窗挡住（{found.blocked}），消息暂不发送"
    elif found.target:
        mode = f"📺 发送到 {found.target.label}（终端实时可见）"
    else:
        mode = "🕶 后台运行（Muxy / tmux 里没有找到运行该 agent 的窗格）"
    return f"📂 {directory}\n🤖 {agent}\n🧵 session: {sid}\n{mode}"


def _run_headless(chat_id: str, agent: str, directory: str, prompt: str, new: bool,
                  images: list) -> None:
    lock = chat_lock(chat_id)
    if lock.locked():
        send_text(chat_id, "⏳ 上一条还在执行，已排队")
    with lock:
        send_text(chat_id, f"▶️ {agent} 开始处理…")
        reply = run_turn(agent, directory, prompt, new=new, images=images)
        send_text(chat_id, reply)


def _post_screen(chat_id: str, target) -> None:
    send_text(chat_id, f"🖥 {target.label}\n\n{screen_text(target)}")


def _terminal_command(chat_id: str, directory: str, agent: str, text: str) -> None:
    """//cmd -> send /cmd to the CLI; /screen -> post screen; /esc -> press Escape."""
    found = find_target(directory, agent)
    if not found.target:
        send_text(chat_id, f"这个命令需要终端窗格：Muxy / tmux 里没有找到运行 {agent} 的窗格。")
        return
    target = found.target
    if text == "/esc":
        press_key(target, "Escape")
    elif text.startswith("//"):
        if found.blocked:
            send_text(chat_id, f"⚠️ {target.label} 里有待确认的弹窗（{found.blocked}），"
                               "命令没有发送。可以先发 /screen 看看，或发 /esc 关掉它。")
            return
        inject(target, text[1:])
    if text != "/screen":
        time.sleep(SCREEN_SETTLE_SEC)
    _post_screen(chat_id, target)


def process(chat_id: str, entry: dict, text: str, images: list = ()) -> bool:
    """Handle one group message; returns True when it was typed into a terminal pane."""
    agent, directory = entry["agent"], entry["dir"]
    if text == "/status":
        send_text(chat_id, _status(directory, agent))
        return False
    if text in TERMINAL_COMMANDS or text.startswith("//"):
        _terminal_command(chat_id, directory, agent, text)
        return False
    new = text.startswith("/new")
    prompt = text[len("/new"):].strip() if new else text
    if not prompt:
        send_text(chat_id, "用法：/new <要做的事>")
        return False
    found = None if new else find_target(directory, agent)
    if found and found.target and found.blocked:
        send_text(chat_id, f"⚠️ {found.target.label} 里有待确认的弹窗（{found.blocked}），"
                           "这条消息没有发送。请先在终端里处理弹窗，再重发。")
        return False
    if found and found.target:
        inject(found.target, with_image_note(prompt, images))
        send_text(chat_id, f"⌨️ 已发送到 {found.target.label}，{agent} 完成后会把结果推送到群里")
        return True
    _run_headless(chat_id, agent, directory, prompt, new, images)
    return False


def handle_incoming(chat_id: str, entry: dict, message_id: str, text: str,
                    image_keys: list) -> None:
    """Download images; park them if there's no text yet, else run the agent turn."""
    media.prune_inbox()
    dest = str(media.INBOX_DIR / chat_id)
    fresh = [download_image(message_id, key, dest) for key in image_keys]
    if not text:
        count = media.add_pending(chat_id, fresh)
        send_text(chat_id, f"📎 已收到 {count} 张图片，请接着发文字说明要做什么")
        return
    images = [*media.take_pending(chat_id), *fresh]
    injected = False
    try:
        injected = process(chat_id, entry, text, images)
    finally:
        if not injected:  # an injected turn reads the images later; prune_inbox handles them
            media.cleanup(images)


def safe_process(chat_id: str, entry: dict, message_id: str, text: str,
                 image_keys: list) -> None:
    try:
        handle_incoming(chat_id, entry, message_id, text, image_keys)
    except Exception as exc:  # noqa: BLE001 - report failure back to the group
        log.exception("process failed chat=%s", chat_id)
        try:
            send_text(chat_id, f"❌ 桥接程序出错：{exc}")
        except Exception:  # noqa: BLE001
            log.exception("could not report error to chat=%s", chat_id)


def on_message(data) -> None:
    event = data.event
    message, sender = event.message, event.sender
    entry = load_json(DIR_MAP_PATH).get(message.chat_id)
    if not entry or sender.sender_type != "user" or not first_time(message.message_id):
        return
    if sender.sender_id.open_id not in allowed_senders(entry):
        log.warning("rejected sender %s in %s", sender.sender_id.open_id, entry["name"])
        return
    text, image_keys = media.parse_content(message.message_type, message.content)
    if not text and not image_keys:
        return
    log.info("[%s] %s (+%d 图)", entry["name"], text[:80], len(image_keys))
    # Return quickly so Feishu doesn't redeliver; run the agent in the background.
    threading.Thread(target=safe_process,
                     args=(message.chat_id, entry, message.message_id, text, image_keys),
                     daemon=True).start()


def main() -> None:
    app_id, app_secret = require_env("FEISHU_APP_ID"), require_env("FEISHU_APP_SECRET")
    if not load_json(DIR_MAP_PATH):
        raise SystemExit("dir_map.json 为空，请先运行 setup_map.py")
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(on_message).build())
    log.info("bridge started, groups: %s",
             [e["name"] for e in load_json(DIR_MAP_PATH).values()])
    lark.ws.Client(app_id, app_secret, event_handler=handler, domain=FEISHU_DOMAIN,
                   log_level=lark.LogLevel.INFO).start()


if __name__ == "__main__":
    main()
