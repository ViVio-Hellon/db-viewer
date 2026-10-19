/*
  一覧。VBA の表示(フィルタ列 + ListView + ページ)は捨て、Web の表にした。

  - 全行をまとめて受け取り、絞り込み・並べ替え・列の設定は画面の中で(grid.js)
  - 右に選んだ行の詳細(隠した列も含めて全部)
  - 帳票・印刷・コピー・保存は「いま見えているとおり」
  判断(誰が何をできるか・登録の決まり)は今までどおりサーバ(レコード画面)。
*/
import { api, tokenUrl } from "../api.js";
import { toast, toastError } from "../toast.js";
import { openWindow } from "../desktop.js";
import { createGrid } from "../grid.js";
import * as out from "../export_ui.js";
import { createHelpers } from "../helpers.js";
import { openAddColumn } from "../addcolumn.js";
import { makeField, inputOf, previewFile } from "../fieldinput.js";
import { actionButton } from "../cellaction.js";
import { openRowForm, showProblem } from "../rowform.js";
import { confirmBox } from "../askbox.js";

const el = {};
const DETAIL_KEY = "dbviewer.list.detail";
let grid = null;
let data = null;            // サーバから受け取った一覧({columns, rows, key_index, setup, ...})
let helpers = null;

function problem(text) {
  el.listProblem.hidden = !text;
  el.listProblem.textContent = text || "";
}

/* ---------------- 読む ---------------- */
const simple = () => data && data.mode === "simple";

async function load(table = "") {
  try {
    data = await api.post("/api/list/data", table ? { table } : {});
    problem("");
    el.listUpdated.hidden = true;
    el.listTitle.textContent = data.table || data.setup.main_table || "一覧";
    renderTables();
    el.actReport.hidden = !data.setup.report_flag;
    if (!data.edit || !data.edit.allowed) {
      el.listHint.textContent = "見出しを押すと並べ替え、▾ で絞り込み。このレベルでは直せません(ダブルクリックで参照)";
    }
    // 管理番号 の列が無い表(設定の無い DB)は、行を直す・足す・消すができない(かんたん設定で足せる)
    const keyOk = simple() || data.setup.key_ok !== false;
    el.actNew.hidden = !keyOk || !data.can.add; el.actEdit.hidden = !keyOk; el.actDelete.hidden = !keyOk || !data.can.delete;
    el.actAddCol.hidden = !data.setup.can_add_column;
    showNotices(data.setup.notices || []);
    grid.setData({ columns: data.columns, rows: data.rows, keyIndex: data.key_index, view: data.view, actions: data.actions });
  } catch (err) {
    if (err.status === 401) { location.href = "/login?next=/list"; return; }
    if (err.status === 409 || err.status === 422) problem(err.message);
    else toastError(err);
  }
}

/** 一覧に出す表(業務の表ぜんぶ)。選ぶとこの PC で覚える。 */
function renderTables() {
  const tables = data.tables || [];
  el.listTable.hidden = tables.length < 2;
  el.listTitle.hidden = !el.listTable.hidden;              // 表を選べるときは選ぶ欄が見出しを兼ねる
  el.listTable.replaceChildren(...tables.map((t) => {
    const o = document.createElement("option");
    o.value = t.name;
    o.textContent = `${t.name}(${t.rows >= 0 ? t.rows.toLocaleString() : "?"} 件)${t.brought ? " 足" : ""}`;
    if (t.brought) o.title = "「表を持ってくる」で足した表";
    o.selected = t.name === data.table;
    return o;
  }));
}

/** 設定が無い・合わないところを、止めずに知らせる(かんたん設定へ)。 */
function showNotices(list) {
  el.listNotice.hidden = !list.length;
  el.listNoticeText.textContent = list.join("\n");
}

function addColumn() {
  if (!data) return;
  openAddColumn({
    table: data.setup.main_table, url: "/api/list/add-column", kinds: data.setup.kinds,
    settingTable: data.setup.setting_table,
    onDone: async (res) => {
      toast(res.message, "ok");
      await load();
      grid.scrollToEnd();
    },
  });
}

// 他の端末が本体を書いた(心拍の db_mtime が、読んだときより新しい)
function onHealth(event) {
  const mtime = event.detail && event.detail.db_mtime;
  if (!mtime || !data || !data.db_mtime) return;
  if (mtime > data.db_mtime + 0.5) el.listUpdated.hidden = false;
}

/* ---------------- 選んだ行・詳細 ---------------- */
function keyOf(row) {
  return row && data && data.key_index >= 0 ? String(row[data.key_index] ?? "") : "";
}

function onSelect(row) {
  for (const b of [el.actEdit, el.actView, el.actDelete]) b.disabled = !row;
  renderDetail(row);
}

function renderDetail(row) {
  if (!row) {
    const p = document.createElement("p");
    p.className = "dt__empty";
    p.textContent = "行を選ぶと、ここに全部の列が出ます。";
    el.detail.replaceChildren(p);
    return;
  }
  const names = data.columns.map((c) => c.name);
  const idx = Object.fromEntries(names.map((n, i) => [n, i]));
  const shown = grid.visibleColumns();
  const hidden = names.filter((n) => !shown.includes(n));
  const head = document.createElement("div");
  head.className = "dt__head";
  const small = document.createElement("small");
  small.textContent = simple() ? `${data.table} の行` : data.key_index >= 0 ? `${names[data.key_index]} ${keyOf(row)}` : "選んだ行";
  const title = document.createElement("b");
  const firstText = shown.find((n) => n !== names[data.key_index] && (row[idx[n]] || "").length > 1);
  title.textContent = firstText ? row[idx[firstText]] : "";
  head.append(small, title);
  const dl = document.createElement("dl");
  dl.className = "dt__list";
  const add = (n, cls = "") => {
    const dt = document.createElement("dt"); dt.textContent = n; if (cls) dt.className = cls;
    const dd = document.createElement("dd"); dd.textContent = row[idx[n]] ?? ""; if (cls) dd.className = cls;
    const spec = data.edit && data.edit.columns[n];
    if (spec && spec.inline === "file" && row[idx[n]]) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn btn--find dt__file"; b.textContent = "プレビュー";
      b.addEventListener("click", () => previewFile(data.table, n, row[idx[n]]));
      dd.appendChild(b);
    }
    const act = actionButton((data.actions || {})[n], row[idx[n]]);
    if (act) dd.appendChild(act);
    dl.append(dt, dd);
  };
  for (const n of shown) add(n);
  if (hidden.length) {
    const sep = document.createElement("div");
    sep.className = "dt__sep";
    sep.textContent = "表で隠している列";
    dl.appendChild(sep);
    for (const n of hidden) add(n, "is-hidden");
  }
  el.detail.replaceChildren(head, dl);
}

function setDetail(on) {
  el.lvBody.classList.toggle("no-detail", !on);
  el.actDetail.setAttribute("aria-pressed", String(on));
  try { localStorage.setItem(DETAIL_KEY, on ? "1" : "0"); } catch { /* 覚えられないだけ */ }
}

/* ---------------- 新規・修正・参照・削除(判断はレコード画面) ---------------- */
function command(mode, click = "ButtonClick") {
  const row = grid.selected();
  if (mode !== "新規" && !row) { toast("対象が選択されていません。", "warn"); return; }
  if (simple()) { simpleCommand(mode, row); return; }
  const q = new URLSearchParams({ mode, click });
  if (mode !== "新規") q.set("key", keyOf(row));
  location.href = `/record?${q}`;
}

/* ---------- VBA 版でない表: 行の窓で足す・直す・見る、消す(行は rowid) ---------- */
function seenOf(row) {
  return Object.fromEntries(data.columns.map((c, i) => [c.name, row[i] ?? ""]));
}

async function simpleCommand(mode, row) {
  const rowid = row ? keyOf(row) : "";
  if (mode === "削除") {
    if (!(await confirmBox("この行を削除しますか？", { ok: "削除する", danger: true }))) { toast("キャンセルしました。", "info"); return; }
    try {
      const res = await api.post("/api/list/row-delete", { table: data.table, rowid, seen: seenOf(row) });
      if (res.db_mtime) data.db_mtime = res.db_mtime;
      toast(res.message, "warn");
      await load();
    } catch (err) { toastError(err, "warn"); if (err.status === 409) await load(); }
    return;
  }
  const readOnly = mode === "参照" || !data.edit.allowed;
  let form;
  try {
    form = await api.post("/api/list/form", { table: data.table, rowid: mode === "新規" ? 0 : rowid });
  } catch (err) { toastError(err); return; }
  const seen = Object.fromEntries(form.fields.map((f) => [f.name, f.value ?? ""]));
  openRowForm({
    title: `${data.table} — ${mode === "新規" ? "行を足す" : readOnly ? "見る" : "直す"}`,
    fields: form.fields, readOnly,
    onPreview: (column, value) => previewFile(data.table, column, value),
    onSave: async (values) => {
      try {
        const res = mode === "新規"
          ? await api.post("/api/list/row-add", { table: data.table, values })
          : await api.post("/api/list/row-save", { table: data.table, rowid, values, seen });
        if (res.db_mtime) data.db_mtime = res.db_mtime;
        toast(res.reason === "unchanged" ? "変わっていません。" : res.message, res.reason === "unchanged" ? "info" : "ok");
        await load();
        if (res.rowid) grid.select(String(res.rowid));
        return true;
      } catch (err) {
        if (err.status && err.status < 500) { showProblem(err.message); return false; }
        toastError(err); return false;
      }
    },
  });
}

/* ---------------- 見えているとおりに取り出す ---------------- */
/** 印刷・出力・コピーする表: 見えている列のうち、かんたん設定で「印刷」の列(並びは見えているとおり)。 */
function visibleTable() {
  const names = data.columns.map((c) => c.name);
  const printSet = data.print_columns ? new Set(data.print_columns) : null;
  const cols = grid.visibleColumns().filter((n) => !printSet || printSet.has(n));
  const idx = cols.map((n) => names.indexOf(n));
  const rows = grid.shownRows().map((r) => idx.map((i) => r[i] ?? ""));
  const numeric = cols.map((n) => data.columns[names.indexOf(n)].kind === "num");
  return { cols, rows, numeric };
}

function filterNote() {
  const all = grid.total(), n = grid.shownRows().length;
  return n === all ? `${all.toLocaleString()} 件` : `${all.toLocaleString()} 件中 ${n.toLocaleString()} 件(絞り込み中)`;
}

async function runReport() {
  // 帳票(全般設定「帳票」)。いま絞り込んでいる行で作る。並びはサーバが帳票の決まりで決める
  const keys = grid.shownKeys();
  if (!keys.length) { toast("帳票にする行がありません。", "warn"); return; }
  try {
    const res = await api.post("/api/report/run", { keys, by: simple() ? "rowid" : "key" });
    // 帳票は別の窓で(デスクトップ版は外枠が窓を開く)
    if (!openWindow(tokenUrl(res.preview), "帳票")) toast("別の窓を開けませんでした。ポップアップの許可を確認してください。", "warn");
  } catch (err) { toastError(err); }
}

/* ---------------- マスを直す(すぐ本体に書く) ---------------- */
function saveState(text, kind = "") {
  el.saveState.textContent = text;
  el.saveState.className = `savestate${kind ? ` is-${kind}` : ""}`;
}

/** マスのダブルクリック。直せる列なら true(その場で直す)、直せなければ false(行を開く)。 */
function onCellEdit(row, name, td) {
  const fileSpec = data && data.edit && data.edit.columns[name];
  if (fileSpec && fileSpec.inline === "file") {                          // ファイルの列はプレビュー(見るだけでもよい)
    previewFile(data.table, name, row[data.columns.findIndex((c) => c.name === name)]);
    return true;
  }
  if (!data || !data.edit || !data.edit.allowed) return false;          // レベルが足りない → 参照で開く
  const spec = data.edit.columns[name];
  if (!spec || !spec.inline) {
    if (spec && spec.why) toast(`${name}: ${spec.why}`, "info");
    else if (!spec) toast(`${name}: この列は一覧で作った列(マスタ結合・追加表示)なので直せません。`, "info");
    return true;
  }
  const key = keyOf(row);
  const names = data.columns.map((c) => c.name);
  const shown = row[names.indexOf(name)] ?? "";
  if (spec.money) {
    // 金額は一覧で "#,##0"(丸め)で見せている。丸めた数から直し始めない(本体の値を取ってくる)
    api.post("/api/list/value", { key, column: name })
      .then((res) => beginEdit(td, key, name, String(res.value ?? ""), spec, row))
      .catch((err) => toastError(err));
    return true;
  }
  beginEdit(td, key, name, shown, spec, row);
  return true;
}

function beginEdit(td, key, name, seen, spec, row) {
  if (spec.inline === "simple") { startSimpleEdit(td, key, name, seen, spec, row); return; }
  if (spec.inline === "pick") {
    // マスタ・日付・数値などは、登録画面と同じ選ぶ窓で
    const inp = document.createElement("input");
    inp.value = seen;
    helpers.open({ ...spec, value: seen, original: seen }, inp).then((res) => {
      if (res !== undefined) saveCell(key, name, res.value, seen, row);
    });
    return;
  }
  startTextEdit(td, key, name, seen, spec, row);
}

function startTextEdit(td, key, name, seen, spec, row) {
  const multi = (spec.lines || 1) > 1 || /\n/.test(seen);
  const input = document.createElement(multi ? "textarea" : "input");
  input.className = "cell-edit";
  input.value = seen;
  td.classList.add("is-editing");
  td.replaceChildren(input);
  input.focus();
  input.select();
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    const v = input.value;
    td.classList.remove("is-editing");
    // やめたときは一覧の見せ方(金額ならカンマ付き)に描き直す
    if (commit) td.textContent = v; else grid.updateRow(key, row);
    if (commit && v !== seen) saveCell(key, name, v, seen, row);
    grid.focus();
  };
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
    else if (e.key === "Enter" && (!multi || e.ctrlKey)) { e.preventDefault(); finish(true); }
    else if (e.key === "Tab") { e.preventDefault(); finish(true); }
  });
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("dblclick", (e) => e.stopPropagation());
}

/** 入力の種類どおりの欄(コンボボックス・スピンボタン・カレンダー)をマスにかぶせる。 */
function startSimpleEdit(td, key, name, seen, spec, row) {
  const field = makeField(spec, seen, { className: "cell-edit" });
  const input = inputOf(field);
  td.classList.add("is-editing");
  td.replaceChildren(field);
  input.focus();
  if (input.type === "text") input.select();
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    const v = input.value;
    td.classList.remove("is-editing");
    grid.updateRow(key, row);
    if (commit && v !== seen && !(spec.input === "calendar" && v === "" && seen === "" )) saveCell(key, name, v, seen, row);
    grid.focus();
  };
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
    else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); finish(true); }
  });
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) finish(true); }, 0));
  input.addEventListener("dblclick", (e) => e.stopPropagation());
}

async function saveCell(key, name, value, seen, row) {
  saveState(`保存しています… ${name}`);
  try {
    const res = simple()
      ? await api.post("/api/list/row-save", { table: data.table, rowid: key, values: { [name]: value }, seen: { [name]: seen } })
      : await api.post("/api/list/cell", { key, column: name, value, seen });
    if (res.db_mtime && data) data.db_mtime = res.db_mtime;        // 自分の書き込みで「他の端末」帯を出さない
    if (res.row) grid.updateRow(key, res.row);
    if (res.reason === "unchanged") { saveState(""); return; }
    const t = new Date();
    saveState(`保存しました ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")} ・ ${name}`, "ok");
    const td = grid.cell(key, name);
    if (td) { td.classList.remove("is-saved"); void td.offsetWidth; td.classList.add("is-saved"); }
  } catch (err) {
    const body = err.body || {};
    if (body.db_mtime && data) data.db_mtime = body.db_mtime;
    if (body.row) grid.updateRow(key, body.row);          // ほかの端末の値に置き換える
    else grid.updateRow(key, row);                        // 元の値に描き直す
    saveState(`保存できませんでした ・ ${name}`, "ng");
    toastError(err, err.status === 409 ? "error" : "warn");
  }
}

export function start() {
  for (const id of ["listTitle", "listProblem", "listUpdated", "listReload", "lvBody", "grid", "detail",
                    "actNew", "actEdit", "actView", "actDelete", "actReport", "actPrint", "actOut",
                    "outMenu", "outCopy", "outSave", "actDetail", "saveState", "listHint", "actAddCol",
                    "listNotice", "listNoticeText", "listTable"]) {
    el[id] = document.getElementById(id);
  }
  grid = createGrid(el.grid, {
    stateKey: "dbviewer.list.grid",
    onSelect,
    onOpen: () => command("修正", "DblClick"),
    onCellEdit,
    onChange: () => {
      if (!grid) return;
      renderDetail(grid.selected());
      try { sessionStorage.setItem("dbviewer.list.keys", JSON.stringify(grid.shownKeys().slice(0, 50000))); } catch { /* 大きすぎれば覚えない */ }
    },
    onViewChange: async (view) => {
      try { await api.post("/api/list/view", view); } catch (err) { toastError(err); }
    },
    onViewReset: async () => {
      try { return (await api.post("/api/list/view", { reset: true })).view; } catch (err) { toastError(err); return null; }
    },
  });

  helpers = createHelpers({ parent: "MF1" });
  el.actNew.addEventListener("click", () => command("新規"));
  el.actEdit.addEventListener("click", () => command("修正"));
  el.actView.addEventListener("click", () => command("参照"));
  el.actDelete.addEventListener("click", () => command("削除"));
  el.actAddCol.addEventListener("click", addColumn);
  el.actReport.addEventListener("click", runReport);
  el.actPrint.addEventListener("click", () => {
    const t = visibleTable();
    if (!t.rows.length) { toast("印刷する行がありません。", "warn"); return; }
    out.printTable({ title: data.setup.main_table, note: filterNote(), columns: t.cols, rows: t.rows, numeric: t.numeric });
  });
  out.wireMenu(el.actOut, el.outMenu);
  el.outCopy.addEventListener("click", () => { const t = visibleTable(); out.copyTable(t.cols, t.rows); });
  el.outSave.addEventListener("click", () => {
    const t = visibleTable();
    if (!t.rows.length) { toast("出す行がありません。", "warn"); return; }
    out.openSaveDialog({ title: data.setup.main_table, columns: t.cols, rows: t.rows });
  });
  el.actDetail.addEventListener("click", () => setDetail(el.lvBody.classList.contains("no-detail")));
  let detailOn = true;
  try { detailOn = localStorage.getItem(DETAIL_KEY) !== "0"; } catch { /* 既定は出す */ }
  setDetail(detailOn);
  el.listReload.addEventListener("click", () => load());
  el.listTable.addEventListener("change", () => { grid.select(null); load(el.listTable.value); });
  document.addEventListener("app:health", onHealth);
  load();
}
