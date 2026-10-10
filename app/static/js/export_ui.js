/*
  export_ui.js — 画面に出している表を「コピー」「ファイルに保存」「印刷」する(一覧・閲覧で共通)

  どれも**見えているとおり**(絞り込み・並べ替え・表示している列・その並び)。
  ダウンロードはしない: ファイルは設定「出力先」のフォルダに書き、場所を知らせる。
*/
import { api, tokenUrl } from "./api.js";
import { toast, toastError } from "./toast.js";
import { openWindow } from "./desktop.js";

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/** 表計算ソフト・メール・チャットに貼れる形(タブ区切り)。タブや改行を含む値は "…" で囲む。 */
export function toTsv(columns, rows) {
  const cell = (v) => {
    const s = String(v ?? "");
    return /[\t\n\r"]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns, ...rows].map((r) => r.map(cell).join("\t")).join("\r\n");
}

export async function copyTable(columns, rows) {
  const text = toTsv(columns, rows);
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // クリップボードが使えないとき(古いブラウザ)。選んでコピーする
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) { toast("コピーできませんでした。", "warn"); return false; }
  }
  toast(`${rows.length.toLocaleString()} 行 × ${columns.length} 列をコピーしました。貼り付けられます。`, "ok");
  return true;
}

export async function printTable({ title, note = "", columns, rows, numeric = [] }) {
  try {
    const res = await api.post("/api/print", { title, note, columns, rows, numeric });
    // 印刷の画面は別の窓で(デスクトップ版は外枠が窓を開く)
    if (!openWindow(tokenUrl(res.preview), "印刷")) toast("別の窓を開けませんでした。ポップアップの許可を確認してください。", "warn");
  } catch (err) { toastError(err); }
}

/* ---------------- ファイルに保存 ---------------- */
let dlg = null;
const ui = {};
let pending = null;

function build() {
  dlg = document.createElement("dialog");
  dlg.className = "browser expdlg";
  dlg.innerHTML = `
    <form method="dialog" class="browser__head"><b>ファイルに保存</b>
      <button class="btn btn--danger" value="cancel">閉じる</button></form>
    <p class="why" data-x="what"></p>
    <div class="expdlg__grid">
      <label for="expName">ファイル名</label>
      <input class="input" id="expName" spellcheck="false" autocomplete="off">
      <span>形式</span>
      <div class="expdlg__opts" role="radiogroup" aria-label="形式">
        <label class="checkline"><input type="radio" name="expFmt" value="csv"> CSV(カンマ区切り)</label>
        <label class="checkline"><input type="radio" name="expFmt" value="tsv"> タブ区切り(.txt)</label>
        <label class="checkline"><input type="radio" name="expFmt" value="xlsx"> Excel 形式(.xlsx)</label>
      </div>
      <label for="expEnc">文字コード</label>
      <select class="input" id="expEnc"></select>
      <span>保存先</span>
      <code data-x="dir" class="expdlg__dir"></code>
    </div>
    <p class="why">保存先と既定の形式は 設定 →「参照・出力」で変えられます。同じ名前があれば _2 を付けます(上書きしません)。</p>
    <div class="btn-row"><button class="btn btn--commit" type="button" data-x="save">保存する</button></div>
    <div class="status status--ok" data-x="done" hidden>
      <span data-x="donetext"></span>
      <button class="btn btn--find" type="button" data-x="open" hidden>フォルダを開く</button>
    </div>`;
  document.body.appendChild(dlg);
  for (const k of ["what", "dir", "save", "done", "donetext", "open"]) ui[k] = dlg.querySelector(`[data-x="${k}"]`);
  ui.name = dlg.querySelector("#expName");
  ui.enc = dlg.querySelector("#expEnc");
  dlg.addEventListener("change", (e) => {
    if (e.target.name === "expFmt") ui.enc.disabled = e.target.value === "xlsx";
  });
  ui.save.addEventListener("click", save);
  ui.name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); save(); } });
  ui.open.addEventListener("click", async () => {
    try { await api.post("/api/export/open", {}); } catch (err) { toastError(err); }
  });
}

async function save() {
  if (!pending) return;
  const fmt = dlg.querySelector("input[name=expFmt]:checked")?.value || "csv";
  try {
    const res = await api.post("/api/export", {
      columns: pending.columns, rows: pending.rows, name: ui.name.value.trim(),
      format: fmt, encoding: ui.enc.value,
    });
    ui.done.hidden = false;
    ui.donetext.textContent = `保存しました: ${res.path}(${res.rows.toLocaleString()} 行)`;
    ui.open.hidden = !res.can_open;
    toast(`保存しました: ${res.path}`, "ok");
  } catch (err) { toastError(err); }
}

export async function openSaveDialog({ title, columns, rows }) {
  if (!dlg) build();
  let info;
  try { info = await api.get("/api/export/settings"); } catch (err) { toastError(err); return; }
  pending = { columns, rows };
  ui.what.textContent = `${title}: ${rows.length.toLocaleString()} 行 × ${columns.length} 列(画面に出している行・列・並びのまま)`;
  ui.name.value = `${title}_${stamp()}`;
  ui.dir.textContent = info.dir;
  ui.enc.replaceChildren(...Object.entries(info.encodings).map(([v, label]) => {
    const o = document.createElement("option"); o.value = v; o.textContent = label; return o;
  }));
  ui.enc.value = info.encoding;
  for (const r of dlg.querySelectorAll("input[name=expFmt]")) r.checked = r.value === info.format;
  ui.enc.disabled = info.format === "xlsx";
  ui.done.hidden = true;
  dlg.showModal();
  ui.name.select();
}

/* ---------------- 出力のメニュー(ボタン + 一覧) ---------------- */
export function wireMenu(button, list) {
  const close = () => { list.hidden = true; button.setAttribute("aria-expanded", "false"); };
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = list.hidden;
    list.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) list.querySelector("button")?.focus();
  });
  list.addEventListener("click", close);
  document.addEventListener("click", (e) => { if (!list.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
}
