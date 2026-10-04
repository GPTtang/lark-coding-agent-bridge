"""Run one headless agent turn (Claude Code or Codex) in a mapped directory."""
import json
import os
import subprocess
import tempfile

from config import (BRIDGE_ENV_FLAG, RUN_TIMEOUT_SEC, SESSIONS_PATH, load_json,
                    normalize_dir, save_json)

CLAUDE_PERMISSION_MODE = os.environ.get("FEISHU_CLAUDE_PERMISSION_MODE", "acceptEdits")
CODEX_SANDBOX = os.environ.get("FEISHU_CODEX_SANDBOX", "workspace-write")
CODEX_BUSY_MARKER = "already has an active writer"
CODEX_BUSY_NOTE = "ℹ️ 原 Codex 会话正在本机打开，已改用新会话处理\n\n"


def current_session(directory: str) -> str:
    return load_json(SESSIONS_PATH).get(normalize_dir(directory), {}).get("session_id", "")


def forget_session(directory: str) -> None:
    sessions = load_json(SESSIONS_PATH)
    key = normalize_dir(directory)
    save_json(SESSIONS_PATH, {k: v for k, v in sessions.items() if k != key})


def with_image_note(prompt: str, images: list) -> str:
    """Agents read attached images from local paths (Claude: Read tool)."""
    if not images:
        return prompt
    listing = "\n".join(f"- {p}" for p in images)
    return f"{prompt}\n\n[用户在 Lark 群里附了图片，请查看以下本地文件]\n{listing}"


def claude_cmd(prompt: str, session_id: str, new: bool, images: list = ()) -> list:
    """Prompt is fed via stdin so text starting with '-' is never parsed as a flag."""
    resume = [] if new else (["--resume", session_id] if session_id else ["--continue"])
    image_dirs = sorted({os.path.dirname(p) for p in images})
    add_dirs = [arg for d in image_dirs for arg in ("--add-dir", d)]
    return ["claude", "-p", *resume, *add_dirs, "--output-format", "json",
            "--permission-mode", CLAUDE_PERMISSION_MODE]


def codex_cmd(prompt: str, session_id: str, new: bool, out_file: str,
              images: list = ()) -> list:
    # --image=PATH form: the flag is variadic, so a bare value list could swallow "-".
    common = ["--skip-git-repo-check", "-o", out_file, *(f"--image={p}" for p in images),
              "-c", f'sandbox_mode="{CODEX_SANDBOX}"', "-c", 'approval_policy="never"']
    if new:
        return ["codex", "exec", *common, "-"]
    target = [session_id] if session_id else ["--last"]
    return ["codex", "exec", "resume", *target, *common, "-"]


def parse_claude_output(stdout: str) -> tuple:
    """Return (reply_text, session_id) from `claude -p --output-format json`."""
    try:
        body = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, ""
    return str(body.get("result", "")), str(body.get("session_id", ""))


def _run(cmd: list, directory: str, prompt: str):
    env = {**os.environ, BRIDGE_ENV_FLAG: "1"}
    return subprocess.run(cmd, cwd=directory, env=env, capture_output=True,
                          text=True, timeout=RUN_TIMEOUT_SEC, input=prompt)


def _codex_busy(proc) -> bool:
    return proc.returncode != 0 and CODEX_BUSY_MARKER in (proc.stderr or proc.stdout or "")


def run_turn(agent: str, directory: str, prompt: str, new: bool = False,
             images: list = ()) -> str:
    if not os.path.isdir(directory):
        return f"❌ 目录不存在：{directory}"
    session_id = current_session(directory)
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        out_file = tmp.name
    try:
        if agent == "claude":
            prompt = with_image_note(prompt, images)
        cmd = (claude_cmd(prompt, session_id, new, images) if agent == "claude"
               else codex_cmd(prompt, session_id, new, out_file, images))
        proc = _run(cmd, directory, prompt)
        note = ""
        if agent == "codex" and not new and _codex_busy(proc):
            note = CODEX_BUSY_NOTE
            proc = _run(codex_cmd(prompt, "", True, out_file, images), directory, prompt)
        if proc.returncode != 0:
            return f"❌ {agent} 退出码 {proc.returncode}\n{(proc.stderr or proc.stdout)[-1500:]}"
        if agent == "claude":
            reply, new_sid = parse_claude_output(proc.stdout)
            if new_sid:
                sessions = load_json(SESSIONS_PATH)
                save_json(SESSIONS_PATH, {**sessions, normalize_dir(directory):
                                          {"agent": agent, "session_id": new_sid}})
            return reply
        with open(out_file, encoding="utf-8") as f:
            return note + f.read()
    except subprocess.TimeoutExpired:
        return f"⏱ {agent} 超过 {RUN_TIMEOUT_SEC // 60} 分钟未完成，已中止"
    finally:
        os.unlink(out_file)
