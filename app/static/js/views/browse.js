/* 閲覧・出力。全般設定「テーブル閲覧」の表(空なら業務の表ぜんぶ)を、一覧と同じ表(grid.js)で見る。
   編集可 なら、見ている表に列を足せる(addcolumn.js)。 */
import { api } from "../api.js";
import { openAddColumn } from "../addcolumn.js";
import { toast, toastError } from "../toast.js";
import { createGrid } from "../grid.js";
import * as out from "../export_ui.js";

const el = {};
let grid = null;
let data = null;
let table = "";
let caps = { can_add_column: false, kinds: [] };

async function loadTables() {
  try {
    const d = await api.post("/api/browse/tables", {});
    el.brTables.replaceChildren(...d.tables.map((t) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = t; b.title = t;
      b.addEventListener("click", () => selectTable(t));
      return b;
    }));
    caps = d;
    el.brNote.textContent = "DB の表をぜんぶ並べています。印刷・保存・コピーに入れる列は 設定 → かんたん設定 の「印刷」で決めます。";
    if (!d.tables.length) el.brNote.textContent = "見られる表がありません。";
    let last = "";
    try { last = sessionStorage.getItem("dbviewer.browse.table") || ""; } catch { /* なし */ }
    if (d.tables.includes(last)) selectTable(last);
  } catch (err) {
    if (err.status === 401) location.href = "/login?next=/browse";
    else if (err.status === 409) { el.brProblem.hidden = false; el.brProblem.textContent = err.message; }
    else toastError(err);
  }
}

async function selectTable(t) {
  table = t;
  try { sessionStorage.setItem("dbviewer.browse.table", t); } catch { /* 覚えられないだけ */ }
  for (const b of el.brTables.children) b.setAttribute("aria-current", String(b.textContent === t));
  try {
    data = await api.post("/api/browse/data", { table: t });
    el.brName.textContent = t;
    el.brPrint.disabled = false; el.brOut.disabled = false;
    el.brAddCol.hidden = !caps.can_add_column;
    const all = data.columns.length, pr = (data.print_columns || []).length;
    el.brPrintCols.textContent = pr < all ? `印刷・保存する列: ${pr} / ${all}` : "";
    if (!grid) {
      grid = createGrid(el.brGrid, {
        onViewChange: async (view) => {
          try { await api.post("/api/browse/view", { table, ...view }); } catch (err) { toastError(err); }
        },
        onViewReset: async () => {
          try { return (await api.post("/api/browse/view", { table, reset: true })).view; } catch (err) { toastError(err); return null; }
        },
      });
    }
    grid.setData({ columns: data.columns, rows: data.rows, keyIndex: -1, view: data.view, actions: data.actions });
  } catch (err) { toastError(err); }
}

/** 印刷・保存・コピーする表。列は かんたん設定の「印刷」の列(表で見せている並び)。 */
function visibleTable() {
  const names = data.columns.map((c) => c.name);
  const printSet = new Set(data.print_columns || names);
  const order = [...grid.visibleColumns(), ...names.filter((n) => !grid.visibleColumns().includes(n))];
  const cols = order.filter((n) => printSet.has(n));
  const idx = cols.map((n) => names.indexOf(n));
  return { cols, rows: grid.shownRows().map((r) => idx.map((i) => r[i] ?? "")),
           numeric: cols.map((n) => data.columns[names.indexOf(n)].kind === "num") };
}

export function start() {
  for (const id of ["brTables", "brName", "brNote", "brProblem", "brGrid", "brPrint", "brOut", "brMenu", "brCopy", "brSave",
                    "brAddCol", "brPrintCols"]) {
    el[id] = document.getElementById(id);
  }
  out.wireMenu(el.brOut, el.brMenu);
  el.brPrint.addEventListener("click", () => {
    const t = visibleTable();
    if (!t.rows.length) { toast("印刷する行がありません。", "warn"); return; }
    out.printTable({ title: table, note: `${t.rows.length.toLocaleString()} 件`, columns: t.cols, rows: t.rows, numeric: t.numeric });
  });
  el.brCopy.addEventListener("click", () => { const t = visibleTable(); out.copyTable(t.cols, t.rows); });
  el.brSave.addEventListener("click", () => {
    const t = visibleTable();
    if (!t.rows.length) { toast("出す行がありません。", "warn"); return; }
    out.openSaveDialog({ title: table, columns: t.cols, rows: t.rows });
  });
  el.brAddCol.addEventListener("click", () => openAddColumn({
    table, url: "/api/browse/add-column", kinds: caps.kinds,
    onDone: async (res) => { toast(res.message, "ok"); await selectTable(table); grid.scrollToEnd(); },
  }));
  loadTables();
}
