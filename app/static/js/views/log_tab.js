/*
  設定「記録」 ── 最近の記録と、記録番号の前後(なぜなぜ分析)

  並べ方・呼び名・下書きの文はサーバ(`dbviewer/presenters/log_view.py`)が決める。
  ここは受け取ったものを並べるだけ。
*/

import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import * as tabs from "../tabs.js";

const el = {};
let lastChain = null;
let loadedOnce = false;

const LV_CLASS = { ERROR: "is-error", WARN: "is-warn" };
// 中身を読む順(いつ → 何が → 誰が・どこで → 結果 → 中身)
const KEY_ORDER = ["t", "lv", "ev", "msg", "rid", "user", "lvl", "pc", "sid", "method", "path", "st", "ms",
                   "data", "exc", "ver"];

async function copyText(text, what) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) { toast("コピーできませんでした。下の欄から選んでコピーしてください。", "warn"); return; }
  }
  toast(`${what}をコピーしました。メール・チャット・Excel に貼り付けられます。`, "ok");
}

function td(text, cls = "") {
  const cell = document.createElement("td");
  if (cls) cell.className = cls;
  cell.textContent = text ?? "";
  return cell;
}

function ridButton(rid) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn ridlink";
  b.textContent = rid;
  b.title = "この記録番号の前後を見る";
  b.dataset.rid = rid;
  return b;
}

function rowNode(r) {
  const tr = document.createElement("tr");
  tr.className = LV_CLASS[r.lv] || "";
  const ridCell = document.createElement("td");
  if (r.rid) ridCell.appendChild(ridButton(r.rid));
  tr.append(td(r.time, "t"), td(r.label, "ev"), td(r.screen), td(r.user), td(r.pc),
            td(r.msg, "msg"), ridCell);
  return tr;
}

function chosenOnly() {
  return document.querySelector("input[name=logOnly]:checked")?.value || "problems";
}

export async function loadEvents() {
  if (!el.rows) return;
  const qs = new URLSearchParams({ days: el.days.value, only: chosenOnly(), q: el.q.value.trim() });
  try {
    const view = await api.get(`/api/log/events?${qs}`);
    loadedOnce = true;
    el.rows.replaceChildren(...view.rows.map(rowNode));
    const pcs = view.pcs.length ? `${view.pcs.length} 台(${view.pcs.join("・")})` : "まだありません";
    el.summary.textContent = `置き場所: ${view.dir} / 記録のある PC: ${pcs} / ${view.rows.length} 件`
      + (view.rows.length >= view.limit ? `(新しい順に ${view.limit} 件まで。言葉で絞ってください)` : "(新しい順)");
    el.empty.hidden = view.rows.length > 0;
    el.empty.className = "status status--ok";
    el.empty.textContent = view.only === "all" ? "この期間の記録はありません。"
      : "この期間に断り・エラーはありません。「すべて」で操作の記録も見られます。";
    el.wrap.hidden = view.rows.length === 0;
  } catch (err) { toastError(err); }
}

function chainItem(r) {
  const li = document.createElement("li");
  li.className = [LV_CLASS[r.lv] || "", r.center ? "is-center" : ""].join(" ").trim();
  const det = document.createElement("details");
  if (r.center) det.open = true;
  const sum = document.createElement("summary");
  const parts = [["t", r.time], ["ev", r.label + (r.st && r.st !== 200 ? ` (${r.st})` : "")],
                 ["sc", r.screen], ["m", [r.user && `${r.user}`, r.msg].filter(Boolean).join(" ― ")]];
  for (const [cls, text] of parts) {
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = text || "";
    sum.appendChild(span);
  }
  const pre = document.createElement("pre");
  // 中身(送った値・理由・例外)。読む順に並べ、トレースバックは別に出す
  const raw = {};
  for (const k of KEY_ORDER) if (k in r.raw) raw[k] = r.raw[k];
  for (const k of Object.keys(r.raw)) if (!(k in raw)) raw[k] = r.raw[k];
  let tb = "";
  if (raw.exc && Array.isArray(raw.exc.tb)) {
    tb = "\n--- 例外の場所(下ほど新しい) ---\n" + raw.exc.tb.join("");
    raw.exc = { type: raw.exc.type, msg: raw.exc.msg };
  }
  pre.textContent = JSON.stringify(raw, null, 2) + tb;
  det.append(sum, pre);
  li.appendChild(det);
  return li;
}

function chainText(view) {
  return view.rows.map((r) => [r.t, r.lv, r.rid, r.label, r.screen, r.st ?? "", r.user, r.pc, r.msg]
    .join("\t")).join("\r\n");
}

export async function openChain(rid) {
  rid = String(rid || "").trim();
  if (!rid) { toast("記録番号を入れてください。", "warn"); return; }
  try {
    const view = await api.get(`/api/log/chain?rid=${encodeURIComponent(rid)}`);
    lastChain = view;
    el.chainTitle.textContent = `記録番号 ${rid} の前後`;
    el.chainNote.hidden = view.found;
    el.chainNote.textContent = view.message || "";
    el.chainList.replaceChildren(...view.rows.map(chainItem));
    el.draft.value = view.draft || "";
    el.copyDraft.disabled = !view.found;
    el.copyChain.disabled = !view.found;
    if (!el.chain.open) el.chain.showModal();
    el.chainList.querySelector(".is-center")?.scrollIntoView({ block: "center" });
  } catch (err) { toastError(err); }
}

export function render(log) {
  if (!log || !el.logDir) return;
  el.logDir.value = log.dir || "";
  if (el.logDirReal) { el.logDirReal.hidden = !log.dir_real; el.logDirReal.textContent = `→ ${log.dir_real}`; }
  el.dirState.className = `lv lv--${log.dir_state.level}`;
  el.dirState.textContent = log.dir_state.text;
  el.problem.hidden = !log.problem;
  el.problem.textContent = log.problem || "";
  el.keep.value = log.keep_days;
  el.file.textContent = `この PC のファイル: ${log.file}(ほかに dbviewer_${log.pc}_日付.log = 開発者向けの詳しいログ)`;
  const n = log.errors_today, w = log.warns_today;
  el.today.className = `lv lv--${n ? "ng" : w ? "warn" : "ok"}`;
  el.today.textContent = n || w ? `今日: エラー ${n} / 断り ${w}` : "今日は問題なし";
  if (loadedOnce) loadEvents();
}

/**
 * @param {object} state 設定画面の状態(`state.log`)
 * @param {(body:object) => Promise} save 設定の保存(settings.js の save)
 */
export function start(state, save) {
  for (const [key, id] of [["rows", "logRows"], ["days", "logDays"], ["q", "logQ"], ["reload", "logReload"],
                           ["summary", "logSummary"], ["empty", "logEmpty"], ["wrap", "logWrap"],
                           ["rid", "logRid"], ["go", "logChainGo"], ["today", "logToday"],
                           ["logDir", "logDir"], ["logDirReal", "logDirReal"], ["dirState", "logDirState"],
                           ["problem", "logDirProblem"], ["keep", "logKeepDays"], ["file", "logFile"],
                           ["saveLog", "saveLog"], ["openLog", "openLog"],
                           ["chain", "logChain"], ["chainTitle", "logChainTitle"], ["chainNote", "logChainNote"],
                           ["chainList", "logChainList"], ["draft", "logDraft"],
                           ["copyDraft", "logCopyDraft"], ["copyChain", "logCopyChain"]]) {
    el[key] = document.getElementById(id);
  }
  if (!el.rows) return;

  el.reload.addEventListener("click", loadEvents);
  el.days.addEventListener("change", loadEvents);
  for (const r of document.querySelectorAll("input[name=logOnly]")) r.addEventListener("change", loadEvents);
  el.q.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); loadEvents(); } });
  el.q.addEventListener("search", loadEvents);
  el.rows.addEventListener("click", (e) => {
    const b = e.target.closest("[data-rid]");
    if (b) openChain(b.dataset.rid);
  });
  el.go.addEventListener("click", () => openChain(el.rid.value));
  el.rid.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); openChain(el.rid.value); } });

  el.saveLog.addEventListener("click", () => save({
    log_dir: el.logDir.value, log_keep_days: Number(el.keep.value === "" ? -1 : el.keep.value),
  }));
  el.openLog.addEventListener("click", async () => {
    try { await api.post("/api/log/open", {}); } catch (err) { toastError(err); }
  });

  el.chain.querySelector("[data-close]").addEventListener("click", () => el.chain.close());
  el.copyDraft.addEventListener("click", () => lastChain && copyText(lastChain.draft || "", "なぜなぜの下書き"));
  el.copyChain.addEventListener("click", () => lastChain && copyText(chainText(lastChain), "前後の並び"));

  // タブを開いたときに読む(開かない人の分は読まない)
  const root = document.getElementById("settingsTabs");
  const maybeLoad = () => { if (!loadedOnce && tabs.current(root) === "log") loadEvents(); };
  root?.addEventListener("click", () => setTimeout(maybeLoad, 0));
  root?.addEventListener("keydown", () => setTimeout(maybeLoad, 0));
  render(state.log);
  maybeLoad();

  // エラーの知らせから来たとき(`/settings?tab=log&rid=…`)
  const wanted = new URLSearchParams(location.search).get("rid");
  if (wanted) {
    if (root) tabs.select(root, "log");
    el.rid.value = wanted;
    loadEvents();
    openChain(wanted);
  }
}
