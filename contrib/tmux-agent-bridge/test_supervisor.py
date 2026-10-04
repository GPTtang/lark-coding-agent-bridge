import pytest

import config
import notify
import tmux_supervisor as sup

ENTRIES = {
    "c1": {"name": "日语音频群", "dir": "/w/juzi", "agent": "claude"},
    "c2": {"name": "twitter群", "dir": "/w/tw", "agent": "codex"},
    "c3": {"name": "公众号群", "dir": "/w/mp", "agent": "claude"},
}


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TMUX_SESSIONS_PATH", tmp_path / "tmux_sessions.json")
    monkeypatch.setattr(notify, "TMUX_SESSIONS_PATH", tmp_path / "tmux_sessions.json")
    monkeypatch.setattr(sup, "TMUX_SESSIONS_PATH", tmp_path / "tmux_sessions.json")
    monkeypatch.setattr(notify, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(notify, "DIR_MAP_PATH", tmp_path / "dir_map.json")
    monkeypatch.setattr(sup, "PAUSE_FLAG", tmp_path / "tmux.paused")
    return tmp_path


def test_agent_command_fresh_and_resume():
    assert sup.agent_command("claude", "") == "claude"
    assert sup.agent_command("claude", "abc-1") == "claude --resume abc-1 || claude"
    fresh = sup.agent_command("codex", "")
    assert fresh == "codex -c check_for_update_on_startup=false"
    resumed = sup.agent_command("codex", "t-9")
    assert resumed == f"codex -c check_for_update_on_startup=false resume t-9 || {fresh}"
    assert "'x; rm -rf ~'" in sup.agent_command("claude", "x; rm -rf ~")


def test_shell_wrap_runs_through_login_interactive_zsh():
    assert sup.shell_wrap("claude --resume a || claude") == \
        "/bin/zsh -lic 'claude --resume a || claude'"


def test_plan_creates_missing_respawns_dead_and_leaves_live_alone():
    live = {sup.session_name("日语音频群"): False, sup.session_name("twitter群"): True}
    actions = sup.plan(ENTRIES, live, {"/w/tw": "t-9"})
    assert [(a.kind, a.name) for a in actions] == [
        ("respawn", "lark-twitter群"), ("create", "lark-公众号群")]
    assert "resume t-9" in actions[0].command


def test_ensure_does_nothing_when_paused(monkeypatch):
    sup.PAUSE_FLAG.write_text("")
    monkeypatch.setattr(sup, "_tmux", lambda *a, **k: pytest.fail("tmux called while paused"))
    assert sup.ensure(ENTRIES) == []


def test_notify_records_tmux_session_only_inside_tmux(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    config.save_json(notify.DIR_MAP_PATH, {"c1": {"name": "群", "dir": str(d), "agent": "claude"}})
    monkeypatch.setenv(config.BRIDGE_ENV_FLAG, "1")  # skip posting
    monkeypatch.delenv("TMUX_PANE", raising=False)
    notify.handle("claude", "stop", {"cwd": str(d), "session_id": "plain"})
    assert config.load_json(config.TMUX_SESSIONS_PATH) == {}
    monkeypatch.setenv("TMUX_PANE", "%4")
    notify.handle("claude", "stop", {"cwd": str(d), "session_id": "in-tmux"})
    assert config.load_json(config.TMUX_SESSIONS_PATH) == {config.normalize_dir(str(d)): "in-tmux"}
