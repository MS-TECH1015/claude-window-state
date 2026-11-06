#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""window_state.py — Claude Code の窓ごとに「いま本人の判断が要るか」をファイルに書くフック

【なぜ作ったか】
Cursor のターミナルで Claude を何本も並べると、窓を移るたびに「この窓はどこまで進んだか、
何を聞かれていたか」を思い出す負担がかかり、並行するほど頭が疲れる。
窓ごとの状態と現在地をファイルに書き出しておけば、Cursor 拡張とステータスラインが色で見せられ、
本人は思い出す代わりに読むだけで済む。

【状態】
  working  作業中。本人は見なくてよい
  needs    本人の判断が要る（質問・承認・選択）
  done     返事は終わったが、判断は要らない

【イベントごとの動き】すべて同じスクリプトで受け、hook_event_name で分ける
  SessionStart      返答の最後に「窓:」の行を書くよう指示を差し込む
  UserPromptSubmit  working。頼んだことの頭を控える
  PreToolUse        AskUserQuestion なら needs
  PostToolUse       needs のままなら working に戻す。許可や質問に答えて作業が再開したため
  Notification      許可・質問の通知なら needs
  Stop              最後の返答の「窓:」行から needs / done と現在地を読む
  SessionEnd        状態ファイルを消す

【安全設計】
何が起きても exit 0。SessionStart 以外は何も出力しない。
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "window_state"
SESSIONS_DIR = Path.home() / ".claude" / "sessions"
STALE_SECONDS = 24 * 3600
SUMMARY_MAX = 60

# 「要 | 〜」「済 | 〜」の行。前の書き方の「窓: 不要 | 〜」も読む。末尾の「| 183k」はトークン量なので、現在地に含めない
MARKER = re.compile(r"^\s*(?:窓\s*[:：]\s*)?(要|不要|済)\s*[|｜]\s*(.+?)(?:\s*[|｜]\s*\d+k)?\s*$")
QUESTION_HINT = re.compile(r"[?？]\s*$|決めてほしい|しましょうか|よいですか|いいですか")
MARKDOWN = re.compile(r"\*\*|`|^[#>\-\s]+")
NEEDS_NOTIFICATIONS ={"permission_prompt", "elicitation_dialog"}

INSTRUCTION = """本人は Claude の会話を何本も並べて使い、会話を移るたびに返答の末尾だけを読んで判断する。返答を終えるときは、末尾に次の4行を、この順で書く。本文の作業報告を読まなくても状況が分かるようにする。
題: この会話で何をしているか。20字以内の名詞句。会話が続くかぎり同じ題を保ち、作業の中身が変わったときだけ変える
要 | 本人に判断してほしいこと | 183k   または   済 | いまの現在地 | 183k
結果: この返答までの結果を1〜2文。ファイル名やコマンドではなく、何が変わったかで書く
判断: 本人の判断が要ることを、重要な順に「・」で1行ずつ、3つまで。無ければ「なし」と1語だけ書く
「要」は質問・承認・選択など、本人が答えないと先へ進めないときだけ。それ以外は「済」。この行は30字前後で、判断の1つ目と同じことを指す。この4行は会話にだけ書き、ファイルには書かない。
「要」「済」の行の最後の数字は、フックが渡す「直前ターン 183k」の数字をそのまま書く。渡されていないときは「| 183k」ごと省く。"""


def state_path(session_id):
    return STATE_DIR / f"{session_id}.json"


def load_state(session_id):
    try:
        return json.loads(state_path(session_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(session_id, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["updated"] = time.time()
    tmp = state_path(session_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_path(session_id))


def parent_map():
    """pid → 親 pid の対応。拡張がターミナルのシェルの pid と突き合わせるのに使う"""
    if os.name == "nt":
        return parent_map_windows()
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True).stdout
    pairs = (line.split() for line in out.splitlines() if line.strip())
    return {int(pid): int(ppid) for pid, ppid in pairs}


def parent_map_windows():
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    TH32CS_SNAPPROCESS = 0x2
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(ProcessEntry)
    parents = {}
    ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
    while ok:
        parents[entry.th32ProcessID] = entry.th32ParentProcessID
        ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
    k32.CloseHandle(snapshot)
    return parents


def claude_pid(session_id):
    """Claude Code が ~/.claude/sessions/<pid>.json に書く会話 ID から、本体の pid を引く。
    Windows ではフックが Git Bash 経由で起動され、そこで親子の連なりが途切れるため、本体から上へたどる"""
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("sessionId") == session_id:
                return int(path.stem)
        except Exception:
            continue
    return None


def ancestor_pids(session_id):
    parents = parent_map()
    chain = []
    pid = claude_pid(session_id) or os.getpid()
    while pid and pid not in chain and len(chain) < 30:
        chain.append(pid)
        pid = parents.get(pid)
    return chain


def shorten(text):
    text = MARKDOWN.sub("", " ".join(text.split())).strip()
    return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 1] + "…"


def read_last_reply(data):
    """Stop の入力に本文があればそれを、無ければ会話記録の末尾から最後の返答を読む"""
    if data.get("last_assistant_message"):
        return data["last_assistant_message"]
    path = data.get("transcript_path")
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 400_000))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        text = assistant_text(line)
        if text:
            return text
    return ""


def assistant_text(line):
    try:
        entry = json.loads(line)
    except Exception:
        return ""
    if entry.get("type") != "assistant":
        return ""
    content = entry.get("message", {}).get("content", [])
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content if c.get("type") == "text").strip()


def judge_reply(reply):
    """返答の「窓:」行から (状態, 現在地) を返す。行が無いときは末尾の問いかけで推し量る"""
    lines = [l for l in reply.splitlines() if l.strip()]
    # 「要」「済」の行の下に「結果:」と「判断:」が最大5行続くので、末尾10行まで探す
    for line in reversed(lines[-10:]):
        m = MARKER.match(line)
        if m:
            return ("needs" if m.group(1) == "要" else "done"), shorten(m.group(2))
    tail = lines[-3:]
    last = shorten(lines[-1]) if lines else ""
    if any(QUESTION_HINT.search(l) for l in tail):
        return "needs", last
    return "done", last


def on_session_start(sid, data, state):
    state.update({"state": "done", "summary": "", "asked": "", "cwd": data.get("cwd", "")})
    state["pids"] = ancestor_pids(sid)
    save_state(sid, state)
    remove_stale()
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": INSTRUCTION}}, ensure_ascii=False))


def on_prompt(sid, data, state):
    # /clear の直後は SessionStart の時点で本体の記録がまだ古いことがあるので、頼むたびに引き直す
    state["pids"] = ancestor_pids(sid)
    state.update({"state": "working", "asked": shorten(data.get("prompt", ""))})
    save_state(sid, state)


def on_pre_tool(sid, data, state):
    if data.get("tool_name") != "AskUserQuestion":
        return
    state.update({"state": "needs", "summary": "質問に答えてください"})
    save_state(sid, state)


def on_post_tool(sid, data, state):
    if state.get("state") != "needs":
        return
    state["state"] = "working"
    save_state(sid, state)


def on_notification(sid, data, state):
    if data.get("notification_type") not in NEEDS_NOTIFICATIONS:
        return
    state.update({"state": "needs", "summary": shorten(data.get("message", "承認待ち"))})
    save_state(sid, state)


def on_stop(sid, data, state):
    status, summary = judge_reply(read_last_reply(data))
    state.update({"state": status, "summary": summary})
    save_state(sid, state)


def on_session_end(sid, data, state):
    state_path(sid).unlink(missing_ok=True)


def remove_stale():
    now = time.time()
    for path in STATE_DIR.glob("*.json"):
        if now - path.stat().st_mtime > STALE_SECONDS:
            path.unlink(missing_ok=True)


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_prompt,
    "PreToolUse": on_pre_tool,
    "PostToolUse": on_post_tool,
    "Notification": on_notification,
    "Stop": on_stop,
    "SessionEnd": on_session_end,
}


def main():
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    sid = data.get("session_id")
    handler = HANDLERS.get(data.get("hook_event_name"))
    if not sid or not handler:
        return
    state = load_state(sid)
    if not state.get("pids"):
        state["pids"] = ancestor_pids(sid)
    handler(sid, data, state)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
