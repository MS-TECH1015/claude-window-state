# claude-window-state

Claude Code を Cursor のターミナルで何本も並べて使うと、窓を移るたびに「この窓はどこまで進んだか、何を聞かれていたか」を思い出すことになる。
窓1本あたりの作業は軽くても、移るたびに文脈を積み直すので、並べるほど頭が疲れる。

この拡張とフックは、思い出す作業を読む作業に置き換える。窓ごとに「判断が要る・判断は要らない・作業中」を記録し、色で見せる。

## 何をするか

### 窓を離れるとき

- その窓の状態と、離れた時刻を控える
- `Ctrl+Alt+M` で、自分の頭にあることを一言メモできる。Claude の記録に残らないのは本人の考えのほうなので、そこだけ自分で残す。次に何かを頼むとメモは消える

### 窓に着いたとき

- ターミナルの枠の色が、その窓の状態の色になる。赤は判断が要る、緑は判断は要らない、青は作業中
- 30秒以上離れていて、そのあいだに状態が変わったか、メモがあるときだけ、変わったことと現在地を1回出す
- ステータスバーの左に、いまの窓の状態と現在地が出る。ポインタを当てると、頼んだことと離れる前のメモも出る

### いつでも

- 各ターミナルのステータスラインの先頭に、状態の色札が出る。分割して並べた窓を見渡すと、どれが判断を待っているかが色だけで分かる
- ステータスバーに「判断待ち N」が出る。`Ctrl+Alt+J` かクリックで、判断待ちの窓へ移れる

## 判断が要るかの見分け方

1. 会話の始めに、返答の最後の行へ `窓: 要 | 本人に決めてほしいこと` か `窓: 不要 | いまの現在地` を書くよう指示を差し込む。この行を読む
2. 許可の確認と AskUserQuestion は、判断が要るとみなす
3. 1 の行が無い返答は、末尾が問いかけかどうかで推し量る

## 入れ方

Windows 11 と Cursor で確かめた。パスは置いた場所に合わせて読み替える。

1. `~/.claude/settings.json` の `hooks` に、次のフックを足す。既にある配列には要素を追加する

```json
"SessionStart":     [{ "hooks": [{ "type": "command", "command": "python \"C:/dev/claude-window-state/hooks/window_state.py\"" }] }],
"SessionEnd":       [{ "hooks": [{ "type": "command", "command": "python \"C:/dev/claude-window-state/hooks/window_state.py\"" }] }],
"UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python \"C:/dev/claude-window-state/hooks/window_state.py\"" }] }],
"Stop":             [{ "hooks": [{ "type": "command", "command": "python \"C:/dev/claude-window-state/hooks/window_state.py\"" }] }],
"PreToolUse":       [{ "matcher": "AskUserQuestion", "hooks": [{ "type": "command", "command": "python \"C:/dev/claude-window-state/hooks/window_state.py\"" }] }],
"PostToolUse":      [{ "matcher": "*", "hooks": [{ "type": "command", "command": "python \"C:/dev/claude-window-state/hooks/window_state.py\"" }] }],
"Notification":     [{ "matcher": "permission_prompt|elicitation_dialog", "hooks": [{ "type": "command", "command": "python \"C:/dev/claude-window-state/hooks/window_state.py\"" }] }]
```

2. ステータスラインの色札を出すなら、ステータスラインのスクリプトの先頭に次の出力を足す

```bash
badge=$(printf '%s' "$input" | python "C:/dev/claude-window-state/hooks/statusline_badge.py")
```

   `statusLine` の `refreshInterval` は入れない。窓1本ごとに数秒おきに python が起動し、窓を5本以上並べると PC が重くなって、ステータスラインが時間切れで空になった。既にステータスラインで python を使っているなら、別に起動せず、その中で `~/.claude/window_state/<session_id>.json` の `state` を読むほうが軽い

3. 拡張を入れる

```bash
npx @vscode/vsce package
cursor --install-extension claude-window-state-0.1.0.vsix
```

4. Claude Code の会話を開き直す。フックは会話の始めに読み込まれる

## 分かっている制限

- ターミナルのタブそのものの色は、拡張から変えられない。Cursor 本体の `workbench.action.terminal.changeColor` は引数を取らず、色を選ぶ一覧を開くだけだった。VS Code 1.128.0 ベースの Cursor で確かめた。そのため、枠とステータスライン、ステータスバーで見せている
- 枠の色は、Cursor のユーザー設定 `workbench.colorCustomizations` を書き換えて出す。元の値は控えておき、Claude の窓から離れると戻す。Cursor のウィンドウを2つ開くと、色を取り合う
- 窓とターミナルの結びつけは、Claude Code が書く `~/.claude/sessions/<pid>.json` を使っている。Windows ではフックが Git Bash 経由で起動され、そこで親子の pid の連なりが途切れるため、Claude 本体の pid から上へたどってターミナルのシェルに行き着く。このファイルは Claude Code の内部のものなので、版が変わると動かなくなるかもしれない
- 返答ごとに「窓:」の1行ぶん、出力が増える

## ライセンス

MIT
