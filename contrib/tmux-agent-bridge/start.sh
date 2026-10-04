#!/bin/zsh
# Start the Feishu <-> Claude Code / Codex bridge in the foreground.
cd "$(dirname "$0")" && exec ./.venv/bin/python bridge.py
