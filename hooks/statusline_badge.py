#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""statusline_badge.py — ステータスラインの先頭に、その窓の状態を色札で出す

並べたターミナルを見渡したとき、どの窓が判断を待っているかを色だけで分かるようにする。
ステータスラインの JSON を標準入力で受け、window_state.py が書いた状態ファイルを読んで、
ANSI の背景色つきの札を1つ出力する。状態ファイルが無ければ何も出さない。

settings.json の statusLine に refreshInterval は入れない。窓の数だけ数秒おきに python が起動し、
窓を5本以上並べると PC が重くなって、ステータスラインが時間切れで空になった。
"""
import json
import sys
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "window_state"
BADGES = {
    # 窓を5本並べると1本の幅が狭いので、札は3文字までにする
    "needs": "\033[41;97m 要判断 \033[0m",
    "done": "\033[42;30m 済 \033[0m",
    "working": "\033[44;97m 作業中 \033[0m",
}


def main():
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    path = STATE_DIR / f"{data.get('session_id', '')}.json"
    if not path.exists():
        return
    state = json.loads(path.read_text(encoding="utf-8"))
    badge = BADGES.get(state.get("state"))
    if badge:
        sys.stdout.write(badge + " ")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
