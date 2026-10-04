import pytest

import injector
import muxy_inject

CLAUDE_SCREEN = """
❯ Try "fix lint errors"
──────────────
  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents
"""
CODEX_SCREEN = """
› Ask Codex to do anything
  GPT-6 medium fast · ~/0twitter-agent
  ← for agents · ? for shortcuts
"""
SHELL_SCREEN = "user@mac ~/proj % ls\nREADME.md\nuser@mac ~/proj % "
CODEX_HOOK_REVIEW = """
  Stop hooks
  1 hook needs review before it can run.
  t trust · esc back
"""
CLAUDE_PERMISSION = """
 Do you want to proceed?
 ❯ 1. Yes
   2. No, and tell Claude what to do differently (esc)
"""


def test_parse_list_panes_tab_separated():
    out = "P1\t✳ Claude Code\t/w/a\ttrue\nP2\tCheck status | x\t/w/b\tfalse\nbroken line\n"
    assert muxy_inject.parse_panes(out) == [("P1", "✳ Claude Code", "/w/a"),
                                            ("P2", "Check status | x", "/w/b")]


def test_agent_detected_from_screen_markers():
    assert injector.agent_on_screen("claude", CLAUDE_SCREEN)
    assert injector.agent_on_screen("codex", CODEX_SCREEN)
    assert not injector.agent_on_screen("claude", SHELL_SCREEN)
    assert not injector.agent_on_screen("codex", SHELL_SCREEN)


def test_blocking_prompts_are_detected():
    assert injector.blocking_prompt(CODEX_HOOK_REVIEW)
    assert injector.blocking_prompt(CLAUDE_PERMISSION)
    assert injector.blocking_prompt(CLAUDE_SCREEN) is None
    assert injector.blocking_prompt(CODEX_SCREEN) is None


def _backend(name, panes, screens):
    return injector.Backend(
        name=name,
        list_panes=lambda: panes,
        read_screen=lambda pane: screens[pane],
        send=lambda pane, text: None,
    )


def test_find_target_prefers_muxy_then_tmux_and_skips_non_agent_panes(tmp_path):
    d = str(tmp_path)
    muxy = _backend("muxy", [("M1", "zsh", d), ("M2", "✳ Claude Code", d)],
                    {"M1": SHELL_SCREEN, "M2": CLAUDE_SCREEN})
    tmux = _backend("tmux", [("%1", "lark", d)], {"%1": CLAUDE_SCREEN})
    found = injector.find_target(d, "claude", [muxy, tmux])
    assert (found.target.backend.name, found.target.pane) == ("muxy", "M2")
    assert found.blocked is None

    only_tmux = injector.find_target(d, "claude", [_backend("muxy", [], {}), tmux])
    assert (only_tmux.target.backend.name, only_tmux.target.pane) == ("tmux", "%1")
    assert injector.find_target(str(tmp_path / "nope"), "claude", [muxy, tmux]).target is None


def test_find_target_reports_blocked_pane_instead_of_injecting(tmp_path):
    d = str(tmp_path)
    muxy = _backend("muxy", [("M1", "codex", d)], {"M1": CODEX_SCREEN + CODEX_HOOK_REVIEW})
    found = injector.find_target(d, "codex", [muxy])
    assert found.target.pane == "M1" and found.blocked


def test_muxy_inject_flattens_newlines_and_presses_enter(monkeypatch):
    calls = []
    monkeypatch.setattr(muxy_inject, "_cli", lambda *args: calls.append(args) or "ok")
    monkeypatch.setattr(muxy_inject.time, "sleep", lambda s: None)
    muxy_inject.send("P1", "第一行\n\n第二行 | 管道")
    assert calls == [("send", "--pane", "P1", "第一行 第二行 | 管道"),
                     ("send-keys", "--pane", "P1", "Enter")]


def test_muxy_unavailable_lists_no_panes(monkeypatch):
    monkeypatch.setattr(muxy_inject, "available", lambda: False)
    assert muxy_inject.list_panes() == []
