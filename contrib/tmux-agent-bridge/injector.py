"""Pick where a group message goes: a Muxy pane, else a tmux pane, else headless.

A pane qualifies when its cwd is the group's directory and its screen shows the
agent's UI. Before typing anything, the bottom of the screen is checked for a
modal (permission dialog, hook review, update prompt): pasted text there would
be read as keystrokes, e.g. a "t" could trust a hook. Such panes are reported as
blocked instead of injected into.
"""
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

import muxy_inject
import tmux_inject
from config import normalize_dir

BOTTOM_LINES = 10   # modals and agent footers live at the bottom of the screen
SCREEN_POST_LINES = 30   # how much of the screen /screen and //cmd post back
_CJK = r"[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef]"

AGENT_MARKERS = {
    "claude": ("shift+tab to cycle", "⏵⏵", "esc to interrupt", "? for shortcuts",
               "bypass permissions"),
    "codex": ("ask codex", "esc to interrupt", "? for shortcuts", "gpt-"),
}
BLOCKING_MARKERS = (
    "do you want to proceed", "do you want to make this edit", "do you want to create",
    "esc to cancel", "needs review", "review required", "t trust", "space/enter toggle",
    "allow command", "would you like to run", "update available", "enter continue",
    "esc close", "esc back", "do you trust", "trust this folder", "press enter to continue",
)


@dataclass(frozen=True)
class Backend:
    name: str
    list_panes: Callable[[], list]          # -> [(pane id, title, cwd)]
    read_screen: Callable[[str], str]
    send: Callable[[str, str], None]
    send_key: Optional[Callable[[str, str], None]] = None   # (pane, key name e.g. "Escape")


@dataclass(frozen=True)
class Target:
    backend: Backend
    pane: str
    title: str

    @property
    def label(self) -> str:
        return f"{self.backend.name}「{self.title or self.pane}」"


@dataclass(frozen=True)
class Found:
    target: Optional[Target] = None
    blocked: Optional[str] = None   # matched modal marker when the pane is busy with a prompt


def _bottom(screen: str) -> str:
    lines = [line for line in screen.splitlines() if line.strip()]
    return "\n".join(lines[-BOTTOM_LINES:]).lower()


def agent_on_screen(agent: str, screen: str) -> bool:
    bottom = _bottom(screen)
    return any(marker in bottom for marker in AGENT_MARKERS.get(agent, ()))


def blocking_prompt(screen: str) -> Optional[str]:
    bottom = _bottom(screen)
    return next((marker for marker in BLOCKING_MARKERS if marker in bottom), None)


def find_target(directory: str, agent: str, backends: list = None) -> Found:
    target_dir = normalize_dir(directory)
    for backend in default_backends() if backends is None else backends:
        for pane_id, title, cwd in backend.list_panes():
            if normalize_dir(cwd) != target_dir:
                continue
            try:
                screen = backend.read_screen(pane_id)
            except Exception:  # noqa: BLE001 - an unreadable pane is just skipped
                continue
            blocked = blocking_prompt(screen)
            if blocked or agent_on_screen(agent, screen):
                return Found(Target(backend, pane_id, title), blocked)
    return Found()


def inject(target: Target, text: str) -> None:
    target.backend.send(target.pane, text)


def press_key(target: Target, key: str) -> None:
    if target.backend.send_key is None:
        raise RuntimeError(f"{target.backend.name} 不支持发送按键")
    target.backend.send_key(target.pane, key)


def collapse_cjk_spacing(text: str) -> str:
    """Muxy's read-screen pads every wide character with one space; drop that padding."""
    return re.sub(rf"({_CJK}) (?=\S)", r"\1", text)


def screen_text(target: Target) -> str:
    lines = [line.rstrip() for line in target.backend.read_screen(target.pane).splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    text = "\n".join(line.strip() if not line.strip() else line for line in lines[-SCREEN_POST_LINES:])
    return collapse_cjk_spacing(re.sub(r"\n{3,}", "\n\n", text)).strip("\n")


# ---- concrete backends ----

def _tmux_panes() -> list:
    return [(pane_id, "tmux " + pane_id, path) for pane_id, path, _ in tmux_inject.list_panes()]


def _tmux_screen(pane_id: str) -> str:
    return subprocess.run([tmux_inject.tmux_bin(), "capture-pane", "-p", "-t", pane_id],
                          capture_output=True, text=True, timeout=10, check=True).stdout


def _tmux_key(pane_id: str, key: str) -> None:
    subprocess.run([tmux_inject.tmux_bin(), "send-keys", "-t", pane_id, key],
                   capture_output=True, timeout=10, check=True)


MUXY = Backend("Muxy", muxy_inject.list_panes, muxy_inject.read_screen, muxy_inject.send,
               muxy_inject.send_key)
TMUX = Backend("tmux", _tmux_panes, _tmux_screen, tmux_inject.inject, _tmux_key)


def default_backends() -> list:
    """FEISHU_INJECT_MODE: auto (Muxy, then tmux) | muxy | tmux | off."""
    mode = os.environ.get("FEISHU_INJECT_MODE", "auto")
    return {"off": [], "muxy": [MUXY], "tmux": [TMUX]}.get(mode, [MUXY, TMUX])
