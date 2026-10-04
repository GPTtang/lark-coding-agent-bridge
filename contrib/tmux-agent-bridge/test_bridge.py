import json
import os
from types import SimpleNamespace

import pytest

import config
import notify
import runner


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(notify, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(runner, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(notify, "DIR_MAP_PATH", tmp_path / "dir_map.json")
    return tmp_path


def test_chat_for_dir_matches_exact_and_subdir_prefers_deepest(tmp_path):
    a, b = tmp_path / "proj", tmp_path / "proj" / "sub"
    b.mkdir(parents=True)
    dir_map = {"c1": {"dir": str(a)}, "c2": {"dir": str(b)}}
    assert config.chat_for_dir(dir_map, str(a))[0] == "c1"
    assert config.chat_for_dir(dir_map, str(b / "x"))[0] == "c2"
    assert config.chat_for_dir(dir_map, str(tmp_path / "projX")) is None


def test_chunk_text_splits_and_handles_empty():
    assert config.chunk_text("abcdef", 4) == ["abcd", "ef"]
    assert config.chunk_text("  ") == ["(无输出)"]


def test_last_assistant_text_from_claude_and_codex_transcripts(tmp_path):
    t = tmp_path / "t.jsonl"
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "claude 回复"}]}},
        {"type": "response_item", "payload": {"role": "assistant",
                                              "content": [{"type": "output_text", "text": "codex 回复"}]}},
        {"type": "user", "message": {"content": "ignored"}},
    ]
    t.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\nnot json\n")
    assert notify.last_assistant_text({"transcript_path": str(t)}) == "codex 回复"
    assert notify.last_assistant_text({"last_assistant_message": "直接给出"}) == "直接给出"


def test_handle_records_session_and_skips_post_inside_bridge(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    config.save_json(notify.DIR_MAP_PATH, {"c1": {"name": "群", "dir": str(d), "agent": "codex"}})
    monkeypatch.setenv(config.BRIDGE_ENV_FLAG, "1")
    notify.handle("codex", "stop", {"cwd": str(d), "thread_id": "abc"})
    assert runner.current_session(str(d)) == "abc"


def test_handle_ignores_unmapped_dir(tmp_path):
    config.save_json(notify.DIR_MAP_PATH, {})
    notify.handle("claude", "stop", {"cwd": str(tmp_path), "session_id": "x"})
    assert not runner.SESSIONS_PATH.exists()


def test_claude_cmd_resume_continue_and_new():
    assert ["--resume", "s1"] == runner.claude_cmd("hi", "s1", False)[2:4]
    assert "hi" not in runner.claude_cmd("hi", "s1", False)
    assert "--continue" in runner.claude_cmd("hi", "", False)
    cmd = runner.claude_cmd("hi", "s1", True)
    assert "--resume" not in cmd and "--continue" not in cmd


def test_codex_cmd_variants():
    assert runner.codex_cmd("hi", "s1", False, "o")[:4] == ["codex", "exec", "resume", "s1"]
    assert runner.codex_cmd("hi", "", False, "o")[3] == "--last"
    new = runner.codex_cmd("hi", "s1", True, "o")
    assert "resume" not in new and new[-1] == "-"


def test_parse_claude_output():
    assert runner.parse_claude_output('{"result":"ok","session_id":"s9"}') == ("ok", "s9")
    assert runner.parse_claude_output("plain") == ("plain", "")


def test_bridge_senders_and_dedupe(monkeypatch):
    import bridge
    assert bridge.media.parse_content("text", json.dumps({"text": "@_user_1 /status"})) == ("/status", [])
    monkeypatch.setenv("FEISHU_ALLOWED_OPEN_IDS", "ou_b, ")
    assert bridge.allowed_senders({"owner_open_id": "ou_a"}) == {"ou_a", "ou_b"}
    assert bridge.first_time("m1") and not bridge.first_time("m1")


def test_codex_resume_conflict_falls_back_to_new_session(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    config.save_json(runner.SESSIONS_PATH, {runner.normalize_dir(str(d)):
                                            {"agent": "codex", "session_id": "busy"}})
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "resume" in cmd:
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="thread busy already has an active writer (code -32600)")
        out_file = cmd[cmd.index("-o") + 1]
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("新会话回复")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    reply = runner.run_turn("codex", str(d), "写一篇推文")
    assert len(calls) == 2 and "resume" not in calls[1]
    assert reply.endswith("新会话回复") and "新会话" in reply.splitlines()[0]


# ---- image support ----
import media


def test_parse_content_text_image_and_post():
    assert media.parse_content("text", json.dumps({"text": "@_user_1 你好"})) == ("你好", [])
    assert media.parse_content("image", json.dumps({"image_key": "img_1"})) == ("", ["img_1"])
    post = {"title": "标题", "content": [
        [{"tag": "at", "user_id": "@_user_1"}, {"tag": "text", "text": "看这张图"}],
        [{"tag": "img", "image_key": "img_2"}]]}
    assert media.parse_content("post", json.dumps(post)) == ("标题\n看这张图", ["img_2"])
    localized = {"zh_cn": post}
    assert media.parse_content("post", json.dumps(localized))[1] == ["img_2"]
    assert media.parse_content("file", "{}") == ("", [])
    assert media.parse_content("text", "not json") == ("", [])


def test_pending_images_are_attached_to_next_text_only_once(tmp_path):
    media.add_pending("c1", [str(tmp_path / "a.png")])
    assert media.take_pending("c1") == [str(tmp_path / "a.png")]
    assert media.take_pending("c1") == []


def test_codex_cmd_attaches_images_before_stdin_marker():
    cmd = runner.codex_cmd("p", "sid", False, "/tmp/o.txt", ["/x/a.png"])
    assert "--image=/x/a.png" in cmd and cmd[-1] == "-"
    assert cmd.index("--image=/x/a.png") < cmd.index("-")


def test_claude_cmd_and_prompt_reference_images(tmp_path):
    img = str(tmp_path / "a.png")
    cmd = runner.claude_cmd("p", "sid", False, [img])
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path)
    prompt = runner.with_image_note("看图", [img])
    assert prompt.startswith("看图") and img in prompt
    assert runner.with_image_note("看图", []) == "看图"


def test_bridge_parks_image_then_sends_it_with_next_text(tmp_path, monkeypatch):
    import bridge
    sent, turns = [], []
    monkeypatch.setattr(media, "INBOX_DIR", tmp_path)
    monkeypatch.setattr(bridge, "send_text", lambda chat, text: sent.append(text))

    def fake_download(mid, key, dest):
        path = tmp_path / f"{key}.png"
        path.write_bytes(b"png")
        return str(path)

    def fake_turn(agent, directory, prompt, new=False, images=()):
        turns.append((prompt, list(images)))
        return "ok"

    monkeypatch.setattr(bridge, "download_image", fake_download)
    monkeypatch.setattr(bridge, "run_turn", fake_turn)
    entry = {"agent": "claude", "dir": str(tmp_path), "name": "群"}
    bridge.handle_incoming("c9", entry, "m1", "", ["img_a"])
    assert turns == [] and "图片" in sent[-1]
    bridge.handle_incoming("c9", entry, "m2", "这是什么", [])
    assert turns == [("这是什么", [str(tmp_path / "img_a.png")])]
    assert not (tmp_path / "img_a.png").exists()  # cleaned up after the turn


# ---- terminal injection (Muxy / tmux) ----
import injector


@pytest.fixture
def wired(tmp_path, monkeypatch):
    import bridge
    calls = SimpleNamespace(sent=[], turns=[], injected=[], found=injector.Found())
    monkeypatch.setattr(bridge, "send_text", lambda chat, text: calls.sent.append(text))
    monkeypatch.setattr(bridge, "run_turn", lambda agent, d, prompt, new=False, images=():
                        calls.turns.append((prompt, new)) or "ok")
    monkeypatch.setattr(bridge, "find_target", lambda d, agent: calls.found)
    monkeypatch.setattr(bridge, "inject",
                        lambda target, text: calls.injected.append((target.pane, text)))
    monkeypatch.setattr(media, "INBOX_DIR", tmp_path / "inbox")
    calls.entry = {"agent": "codex", "dir": str(tmp_path), "name": "群"}
    calls.bridge = bridge
    return calls


def _found(pane="P7", blocked=None):
    backend = injector.Backend("Muxy", list, str, lambda p, t: None)
    return injector.Found(injector.Target(backend, pane, "✳ Claude Code"), blocked)


def test_message_is_injected_into_terminal_pane_when_one_runs_the_agent(wired, tmp_path):
    wired.found = _found()
    img = tmp_path / "a.png"
    img.write_bytes(b"png")
    wired.bridge.process("c1", wired.entry, "看看这个", [str(img)])
    assert wired.turns == []
    pane, text = wired.injected[0]
    assert pane == "P7" and text.startswith("看看这个") and str(img) in text
    assert "Muxy" in wired.sent[-1]


def test_blocked_pane_gets_nothing_typed_and_group_is_told(wired):
    wired.found = _found(blocked="needs review")
    wired.bridge.process("c1", wired.entry, "t", [])
    assert wired.injected == [] and wired.turns == []
    assert "弹窗" in wired.sent[-1] and "needs review" in wired.sent[-1]


def test_injected_images_are_kept_for_the_terminal_session(wired, tmp_path, monkeypatch):
    wired.found = _found()
    monkeypatch.setattr(wired.bridge, "download_image",
                        lambda mid, key, dest: str(tmp_path / f"{key}.png"))
    (tmp_path / "k.png").write_bytes(b"png")
    wired.bridge.handle_incoming("c1", wired.entry, "m1", "这是什么", ["k"])
    assert (tmp_path / "k.png").exists()


def test_falls_back_to_headless_without_pane_and_new_is_always_headless(wired):
    wired.bridge.process("c1", wired.entry, "继续", [])
    wired.found = _found()
    wired.bridge.process("c1", wired.entry, "/new 从头开始", [])
    assert wired.turns == [("继续", False), ("从头开始", True)]
    assert wired.injected == []


def test_status_reports_injection_target(wired):
    wired.found = _found()
    wired.bridge.process("c1", wired.entry, "/status", [])
    assert "Muxy" in wired.sent[-1] and "Claude Code" in wired.sent[-1]


def test_load_groups_reads_json_and_rejects_bad_agents(tmp_path):
    good = tmp_path / "groups.json"
    good.write_text(json.dumps({"群A": {"dir": "~/proj", "agent": "codex"}}, ensure_ascii=False))
    assert config.load_groups(good) == {"群A": (os.path.expanduser("~/proj"), "codex")}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"群B": {"dir": "/x", "agent": "gpt"}}, ensure_ascii=False))
    with pytest.raises(SystemExit):
        config.load_groups(bad)
    with pytest.raises(SystemExit):
        config.load_groups(tmp_path / "missing.json")


# ---- CLI passthrough: //cmd, /screen, /esc ----
@pytest.fixture
def term(wired, monkeypatch):
    wired.keys, wired.screen = [], "  ⏵⏵ auto mode on\n 状 态 面 板"
    backend = injector.Backend("Muxy", list, lambda pane: wired.screen,
                               lambda p, t: None, lambda p, k: wired.keys.append((p, k)))
    wired.found = injector.Found(injector.Target(backend, "P7", "✳ Claude Code"))
    monkeypatch.setattr(wired.bridge, "SCREEN_SETTLE_SEC", 0)
    return wired


def test_double_slash_sends_cli_command_and_posts_screen(term):
    term.bridge.process("c1", term.entry, "//status", [])
    assert term.injected == [("P7", "/status")]
    assert "状态面板" in term.sent[-1]  # Muxy's spaced CJK is collapsed


def test_screen_and_esc_commands(term):
    term.bridge.process("c1", term.entry, "/screen", [])
    assert "状态面板" in term.sent[-1] and term.injected == []
    term.found = injector.Found(term.found.target, blocked="esc to cancel")
    term.bridge.process("c1", term.entry, "/esc", [])
    assert term.keys == [("P7", "Escape")]


def test_cli_commands_need_a_terminal_pane(term):
    term.found = injector.Found()
    term.bridge.process("c1", term.entry, "//compact", [])
    assert term.injected == [] and term.turns == [] and "终端" in term.sent[-1]


def test_double_slash_refused_while_modal_is_open(term):
    term.found = injector.Found(term.found.target, blocked="needs review")
    term.bridge.process("c1", term.entry, "//model", [])
    assert term.injected == [] and "/esc" in term.sent[-1]
