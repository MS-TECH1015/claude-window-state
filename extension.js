// claude-window-state — Claude Code の窓を並べて使うときの、離れる・着くの負担を減らす拡張
//
// hooks/window_state.py が ~/.claude/window_state/<session_id>.json に書く状態を読み、
//   着いたとき: ターミナルの枠を状態の色にし、離れていたあいだに変わったことと現在地を出す
//   離れるとき: その窓の状態を控える。本人の頭にあることを一言メモできる
//   いつでも: ステータスバーに、いまの窓の状態と、判断待ちの窓の数を出す
// ターミナルのタブそのものの色は拡張の API から変えられないため、枠とステータスバーで見せる。

const vscode = require('vscode');
const fs = require('fs');
const os = require('os');
const path = require('path');

const STATE_DIR = path.join(os.homedir(), '.claude', 'window_state');
const BRIEF_AFTER_MS = 30 * 1000;
const FRAME_KEYS = [
  'terminal.border',
  'terminal.tab.activeBorder',
  'panel.border',
  'panelTitle.activeBorder',
  'tab.activeBorderTop',
];
const LOOKS = {
  needs: { label: '判断が要る', color: '#e5484d', icon: '$(circle-filled)' },
  done: { label: '判断は要らない', color: '#30a46c', icon: '$(pass-filled)' },
  working: { label: '作業中', color: '#3e63dd', icon: '$(sync~spin)' },
};

let context;
let sessions = new Map();
let terminalsBySid = new Map();
const shellPids = new WeakMap();
const visits = new Map();
let activeSid = null;
let paintedState;
let currentItem;
let waitingItem;

function activate(ctx) {
  context = ctx;
  fs.mkdirSync(STATE_DIR, { recursive: true });

  currentItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 1000);
  currentItem.command = 'claudeWindowState.leaveMemo';
  waitingItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 999);
  waitingItem.command = 'claudeWindowState.pickWaiting';

  const watcher = fs.watch(STATE_DIR, debounce(refresh, 200));
  ctx.subscriptions.push(
    currentItem,
    waitingItem,
    { dispose: () => watcher.close() },
    vscode.window.onDidChangeActiveTerminal(switchTo),
    vscode.window.onDidOpenTerminal(refresh),
    vscode.window.onDidCloseTerminal(refresh),
    vscode.window.onDidChangeWindowState(onWindowFocus),
    vscode.commands.registerCommand('claudeWindowState.leaveMemo', leaveMemo),
    vscode.commands.registerCommand('claudeWindowState.pickWaiting', pickWaiting),
  );
  refresh();
}

function deactivate() {
  return paintFrame(null);
}

async function refresh() {
  sessions = readSessions();
  terminalsBySid = new Map();
  for (const terminal of vscode.window.terminals) {
    const sid = await sessionOf(terminal);
    if (sid) terminalsBySid.set(sid, terminal);
  }
  if (activeSid === null) activeSid = await sessionOf(vscode.window.activeTerminal);
  render();
}

function readSessions() {
  const result = new Map();
  for (const name of fs.readdirSync(STATE_DIR)) {
    if (!name.endsWith('.json')) continue;
    try {
      const state = JSON.parse(fs.readFileSync(path.join(STATE_DIR, name), 'utf8'));
      result.set(state.session_id || name.slice(0, -5), state);
    } catch {
      // 書き込みの途中に読んだときは次の更新で拾う
    }
  }
  return result;
}

// ターミナルのシェルの pid が、フックの祖先 pid に含まれる会話をその窓の会話とみなす
async function sessionOf(terminal) {
  if (!terminal) return null;
  if (!shellPids.has(terminal)) shellPids.set(terminal, await terminal.processId);
  const pid = shellPids.get(terminal);
  let found = null;
  for (const [sid, state] of sessions) {
    if (!(state.pids || []).includes(pid)) continue;
    if (!found || state.updated > sessions.get(found).updated) found = sid;
  }
  return found;
}

async function switchTo(terminal) {
  leave(activeSid);
  activeSid = await sessionOf(terminal);
  arrive(activeSid);
  render();
}

function onWindowFocus(windowState) {
  if (windowState.focused) arrive(activeSid);
  else leave(activeSid);
}

function leave(sid) {
  const state = sessions.get(sid);
  if (!state) return;
  visits.set(sid, { leftAt: Date.now(), seenUpdated: state.updated, seenState: state.state });
}

function arrive(sid) {
  const visit = visits.get(sid);
  const state = sessions.get(sid);
  visits.delete(sid);
  if (!visit || !state) return;
  const awayMs = Date.now() - visit.leftAt;
  if (awayMs < BRIEF_AFTER_MS) return;

  const changed = state.updated !== visit.seenUpdated;
  const memo = memoFor(sid);
  if (!changed && !memo) return;

  const parts = [`${Math.round(awayMs / 60000)}分ぶり。`];
  if (changed) parts.push(`離れているあいだに ${labelOf(visit.seenState)} → ${labelOf(state.state)}。`);
  if (state.summary) parts.push(`現在地: ${state.summary}`);
  if (memo) parts.push(`／離れる前のメモ: ${memo}`);
  const show = state.state === 'needs' ? vscode.window.showWarningMessage : vscode.window.showInformationMessage;
  show(parts.join(' '));
}

function render() {
  const state = sessions.get(activeSid);
  paintFrame(state ? state.state : null);
  renderCurrent(state);
  renderWaiting();
}

function renderCurrent(state) {
  if (!state || !LOOKS[state.state]) {
    currentItem.hide();
    return;
  }
  const look = LOOKS[state.state];
  currentItem.text = `${look.icon} ${look.label}${state.summary ? ': ' + state.summary : ''}`;
  currentItem.backgroundColor = state.state === 'needs'
    ? new vscode.ThemeColor('statusBarItem.errorBackground')
    : undefined;
  const memo = memoFor(activeSid);
  currentItem.tooltip = [
    state.asked ? `頼んだこと: ${state.asked}` : '',
    state.summary ? `現在地: ${state.summary}` : '',
    memo ? `離れる前のメモ: ${memo}` : '',
    'クリックで、この窓を離れる前のメモを書く',
  ].filter(Boolean).join('\n');
  currentItem.show();
}

function renderWaiting() {
  const count = waitingSids().length;
  if (count === 0) {
    waitingItem.hide();
    return;
  }
  waitingItem.text = `$(bell-dot) 判断待ち ${count}`;
  waitingItem.tooltip = 'クリックで、判断待ちの窓へ移る';
  waitingItem.show();
}

function waitingSids() {
  return [...terminalsBySid.keys()].filter((sid) => sessions.get(sid)?.state === 'needs');
}

async function pickWaiting() {
  const items = waitingSids().map((sid) => ({
    label: terminalsBySid.get(sid).name,
    description: sessions.get(sid).summary || '',
    sid,
  }));
  const picked = await vscode.window.showQuickPick(items, { placeHolder: '判断待ちの窓' });
  if (picked) terminalsBySid.get(picked.sid).show();
}

// 本人の頭にあることは Claude の記録に残らないので、離れる前に自分で一言残せるようにする。
// 次に何かを頼むと、その窓の「頼んだこと」が変わるので、メモは役目を終えたとみなして消す。
async function leaveMemo() {
  const state = sessions.get(activeSid);
  if (!state) {
    vscode.window.showInformationMessage('いまの窓は Claude の窓として見つかっていません');
    return;
  }
  const text = await vscode.window.showInputBox({
    prompt: 'この窓に戻ったときに思い出したいこと',
    value: memoFor(activeSid) || '',
  });
  if (text === undefined) return;
  const memos = context.globalState.get('memos', {});
  memos[activeSid] = { text, asked: state.asked || '' };
  await context.globalState.update('memos', memos);
  render();
}

function memoFor(sid) {
  const memo = context.globalState.get('memos', {})[sid];
  const state = sessions.get(sid);
  if (!memo || !state) return '';
  return memo.asked === (state.asked || '') ? memo.text : '';
}

// 枠の色は Cursor のユーザー設定の workbench.colorCustomizations を書き換えて出す。
// 本人が元から入れていた値は globalState に控え、Claude の窓から離れたら戻す。
async function paintFrame(stateName) {
  const target = stateName && LOOKS[stateName] ? stateName : null;
  if (target === paintedState) return;
  paintedState = target;

  const config = vscode.workspace.getConfiguration('workbench');
  const colors = { ...(config.inspect('colorCustomizations')?.globalValue || {}) };
  const original = context.globalState.get('originalFrame') || pick(colors, FRAME_KEYS);
  await context.globalState.update('originalFrame', target ? original : undefined);

  for (const key of FRAME_KEYS) {
    const value = target ? LOOKS[target].color : original[key];
    if (value === undefined) delete colors[key];
    else colors[key] = value;
  }
  await config.update('colorCustomizations', colors, vscode.ConfigurationTarget.Global);
}

function pick(object, keys) {
  return Object.fromEntries(keys.filter((k) => k in object).map((k) => [k, object[k]]));
}

function labelOf(stateName) {
  return LOOKS[stateName]?.label || '不明';
}

function debounce(fn, ms) {
  let timer;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(fn, ms);
  };
}

module.exports = { activate, deactivate };
