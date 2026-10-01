/* 閲覧・出力。全般設定「テーブル閲覧」の表を、一覧と同じ表(grid.js)で見る。 */
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { createGrid } from "../grid.js";
import * as out from "../export_ui.js";

const el = {};
let grid = null;
let data = null;
let table = "";

async function loadTables() {
  try {
    const d = await api.post("/api/browse/tables", {});
    el.brTables.replaceChildren(...d.tables.map((t) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = t; b.title = t;
      b.addEventListener("click", () => selectTable(t));
      return b;
    }));
    if (!d.browse_flag) {
      el.brNote.textContent = "全般設定「テーブル閲覧」が空なので、閲覧する表はありません(マスタ管理の 全般設定 で足せます)。一覧の表は一覧画面の「出力」から出せます。";
    }
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
    grid.setData({ columns: data.columns, rows: data.rows, keyIndex: -1, view: data.view });
  } catch (err) { toastError(err); }
}

function visibleTable() {
  const names = data.columns.map((c) => c.name);
  const cols = grid.visibleColumns();
  const idx = cols.map((n) => names.indexOf(n));
  return { cols, rows: grid.shownRows().map((r) => idx.map((i) => r[i] ?? "")),
           numeric: cols.map((n) => data.columns[names.indexOf(n)].kind === "num") };
}

export function start() {
  for (const id of ["brTables", "brName", "brNote", "brProblem", "brGrid", "brPrint", "brOut", "brMenu", "brCopy", "brSave"]) {
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
  loadTables();
}
