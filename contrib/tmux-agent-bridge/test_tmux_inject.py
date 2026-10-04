import os
import time
from types import SimpleNamespace

import pytest

import media
import tmux_inject

CLAUDE_BIN = "/Users/u/.local/share/claude/versions/2.1.289"
CODEX_BIN = "/usr/local/lib/node_modules/@openai/codex/vendor/codex/codex"


@pytest.fixture
def tree(tmp_path):
    proj, other = tmp_path / "proj", tmp_path / "other"
    proj.mkdir()
    other.mkdir()
    panes = [("%1", str(other), 100), ("%2", str(proj), 200), ("%3", str(proj), 300)]
    procs = {
        100: (1, "-zsh"), 101: (100, CLAUDE_BIN),            # claude, wrong dir
        200: (1, "-zsh"), 201: (200, "vim notes.md"),        # right dir, no agent
        300: (1, "-zsh"), 301: (300, f"node /usr/local/bin/codex"),
        302: (301, CODEX_BIN), 303: (300, f"{CLAUDE_BIN} --continue"),
    }
    return SimpleNamespace(proj=str(proj), other=str(other), panes=panes, procs=procs)


def test_find_pane_matches_dir_and_agent_in_process_tree(tree):
    assert tmux_inject.find_pane(tree.proj, "codex", tree.panes, tree.procs) == "%3"
    assert tmux_inject.find_pane(tree.proj, "claude", tree.panes, tree.procs) == "%3"
    assert tmux_inject.find_pane(tree.other, "claude", tree.panes, tree.procs) == "%1"
    assert tmux_inject.find_pane(tree.other, "codex", tree.panes, tree.procs) is None


def test_agent_matchers_do_not_confuse_paths_or_editors():
    assert tmux_inject.is_agent_command("claude", "claude --continue")
    assert tmux_inject.is_agent_command("claude", CLAUDE_BIN)
    assert not tmux_inject.is_agent_command("claude", "vim /Users/u/.claude/settings.json")
    assert tmux_inject.is_agent_command("codex", CODEX_BIN)
    assert not tmux_inject.is_agent_command("codex", "grep codex README.md")


def test_inject_uses_bracketed_paste_then_enter(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_inject, "_tmux", lambda args, stdin=None: calls.append((args, stdin)))
    monkeypatch.setattr(tmux_inject.time, "sleep", lambda s: None)
    tmux_inject.inject("%3", "第一行\n第二行")
    assert calls[0] == (["load-buffer", "-b", tmux_inject.BUFFER, "-"], "第一行\n第二行")
    assert calls[1][0] == ["paste-buffer", "-p", "-d", "-b", tmux_inject.BUFFER, "-t", "%3"]
    assert calls[2][0] == ["send-keys", "-t", "%3", "Enter"]


def test_parse_ps_and_panes_output():
    procs = tmux_inject.parse_ps("  10     1 -zsh\n  11    10 claude --continue\nbad line\n")
    assert procs == {10: (1, "-zsh"), 11: (10, "claude --continue")}
    panes = tmux_inject.parse_panes("%1\t/a b\t10\nbroken\n")
    assert panes == [("%1", "/a b", 10)]


def test_prune_inbox_removes_only_old_files(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "INBOX_DIR", tmp_path)
    old, new = tmp_path / "c" / "old.png", tmp_path / "c" / "new.png"
    old.parent.mkdir()
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - media.INBOX_MAX_AGE_SEC - 10
    os.utime(old, (past, past))
    media.prune_inbox()
    assert not old.exists() and new.exists()
