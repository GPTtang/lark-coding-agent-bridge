"""Shared paths, constants and JSON state helpers for the Feishu bridge."""
import json
import os
import pathlib
import tempfile

HOME = pathlib.Path(__file__).resolve().parent
DIR_MAP_PATH = HOME / "dir_map.json"      # chat_id -> {name, dir, agent}
SESSIONS_PATH = HOME / "sessions.json"    # dir -> {agent, session_id}
TMUX_SESSIONS_PATH = HOME / "tmux_sessions.json"  # dir -> session id of the agent hosted in tmux
LOG_PATH = HOME / "bridge.log"
ENV_PATH = HOME / ".env"                  # FEISHU_APP_ID / FEISHU_APP_SECRET (chmod 600)

# Lark (international) by default; set FEISHU_DOMAIN=https://open.feishu.cn for Feishu China.
FEISHU_DOMAIN = os.environ.get("FEISHU_DOMAIN", "https://open.larksuite.com").rstrip("/")
FEISHU_BASE = f"{FEISHU_DOMAIN}/open-apis"
HTTP_TIMEOUT = 15
MAX_MSG_CHARS = 3500          # split long replies into chunks of this size
RUN_TIMEOUT_SEC = 30 * 60     # max time for one headless agent turn
BRIDGE_ENV_FLAG = "FEISHU_BRIDGE_RUN"  # set while bridge runs an agent, so hooks don't double-post

GROUPS_PATH = HOME / "groups.json"        # group name -> {dir, agent}; see groups.example.json
AGENTS = ("claude", "codex")


def _read_env_file() -> dict:
    """KEY=VALUE lines from .env next to this file (GUI apps don't see ~/.zshrc)."""
    if not ENV_PATH.exists():
        return {}
    pairs = (line.split("=", 1) for line in ENV_PATH.read_text(encoding="utf-8").splitlines()
             if "=" in line and not line.lstrip().startswith("#"))
    return {k.strip().removeprefix("export "): v.strip().strip("'\"") for k, v in pairs}


def require_env(name: str) -> str:
    value = (os.environ.get(name) or _read_env_file().get(name, "")).strip()
    if not value:
        raise SystemExit(f"缺少 {name}：请写入 {ENV_PATH}（KEY=VALUE 每行一个）")
    return value


def load_groups(path: pathlib.Path = None) -> dict:
    """groups.json -> {group name: (working dir, agent)}. Rerun setup_map.py after editing."""
    path = GROUPS_PATH if path is None else path
    if not path.exists():
        raise SystemExit(f"缺少 {path}：复制 groups.example.json 为 groups.json 并填写群名、目录和 agent")
    groups = {}
    for name, item in load_json(path).items():
        agent = item.get("agent", "")
        if agent not in AGENTS:
            raise SystemExit(f"{path}: 群「{name}」的 agent 必须是 {' / '.join(AGENTS)}，当前是 {agent!r}")
        groups[name] = (os.path.expanduser(item.get("dir", "")), agent)
    return groups


def load_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} 不是合法 JSON：{exc}") from exc


def save_json(path: pathlib.Path, data: dict) -> None:
    """Atomic write so concurrent hooks never leave a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def normalize_dir(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path)) if path else ""


def chat_for_dir(dir_map: dict, cwd: str):
    """Return (chat_id, entry) whose dir equals cwd or is its closest ancestor."""
    cwd = normalize_dir(cwd)
    best = None
    for chat_id, entry in dir_map.items():
        d = normalize_dir(entry["dir"])
        if cwd == d or cwd.startswith(d + os.sep):
            if best is None or len(d) > len(normalize_dir(best[1]["dir"])):
                best = (chat_id, entry)
    return best


def chunk_text(text: str, size: int = MAX_MSG_CHARS) -> list:
    text = text.strip() or "(无输出)"
    return [text[i:i + size] for i in range(0, len(text), size)]
