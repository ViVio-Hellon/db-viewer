/* 閲覧＆出力(VBA MF3 / ExportData)。一覧からの「出力」もこの画面の出力ダイアログを使う。 */
import { api, tokenUrl } from "../api.js";
import { toast, toastError } from "../toast.js";

const el = {};
const state = { table: "", tables: [], exportDir: "", defaultName: "", from: "" };

async function loadTables() {
  try {
    const d = await api.post("/api/browse/tables", {});
    state.tables = d.tables; state.exportDir = d.export_dir; state.defaultName = d.default_name;
    el.exportDir.textContent = d.export_dir;
    el.brTables.replaceChildren(...d.tables.map((t) => {
      const div = document.createElement("div"); div.textContent = t;
      div.addEventListener("click", () => selectTable(t, div));
      return div;
    }));
    if (!d.browse_flag) el.brNote.textContent = "全般設定「テーブル閲覧」が空なので、閲覧する表はありません。一覧の「出力」はここから出せます。";
    if (state.from === "list") exportDialog("list");
  } catch (err) { if (err.status === 401) location.href = "/login?next=/browse"; else toastError(err); }
}

async function selectTable(t, node) {
  for (const x of el.brTables.children) x.removeAttribute("aria-selected");
  node.setAttribute("aria-selected", "true");
  state.table = t;
  await loadRows();
}

async function loadRows() {
  if (!state.table) return;
  try {
    const d = await api.post("/api/browse/rows", { table: state.table });
    el.brName.textContent = d.table; el.brCount.textContent = `${d.count} 件`;
    const thead = el.brTable.tHead; const tbody = el.brTable.tBodies[0];
    const tr = document.createElement("tr");
    for (const c of d.columns) { const th = document.createElement("th"); th.textContent = c; th.className = "l"; tr.appendChild(th); }
    thead.replaceChildren(tr);
    tbody.replaceChildren(...d.rows.map((row) => { const r = document.createElement("tr"); row.forEach((v) => { const td = document.createElement("td"); td.textContent = v; td.className = "l"; r.appendChild(td); }); return r; }));
    el.brExport.hidden = false; el.brPrint.hidden = false;
  } catch (err) { toastError(err); }
}

let exportSource = "table";
function exportDialog(source) {
  exportSource = source;
  el.exportWhat.textContent = source === "list" ? "ListViewに表示されているデータを出力しますか？" : `表 [${state.table}] を出力しますか？`;
  el.exportName.value = state.defaultName;
  el.exportDialog.showModal();
}

async function runExport(force = false) {
  const body = { source: exportSource, ext: el.exportExt.value, name: el.exportName.value, force };
  if (exportSource === "table") body.table = state.table;
  else { try { body.sql = JSON.parse(sessionStorage.getItem("dbviewer.list") || "{}").sql || ""; } catch (e) { body.sql = ""; } }
  try {
    const res = await api.post("/api/export", body);
    if (!res.ok && res.reason === "question") {
      if (confirm(res.message)) return runExport(true);
      toast("出力がキャンセルされました", "info"); return;
    }
    el.exportDialog.close();
    toast(`${res.message}\n${res.path}`, "ok");
  } catch (err) { toastError(err); }
}

export function start(from) {
  for (const id of ["brTables", "brName", "brCount", "brRefresh", "brExport", "brPrint", "brClose", "brTable", "brNote", "brProblem",
                    "exportDialog", "exportWhat", "exportName", "exportExt", "exportDir", "exportOk"]) el[id] = document.getElementById(id);
  state.from = from || "";
  el.brRefresh.addEventListener("click", loadTables);
  el.brExport.addEventListener("click", () => exportDialog("table"));
  el.brPrint.addEventListener("click", () => window.open(tokenUrl(`/report/table?table=${encodeURIComponent(state.table)}`), "_blank", "noopener"));
  el.exportOk.addEventListener("click", () => runExport(false));
  el.exportDialog.addEventListener("close", () => { if (state.from === "list" && !state.table) { /* 一覧から来て閉じた */ } });
  // VBA MF3.QueryClose: 一覧を作り直してフィルタを消す
  el.brClose.addEventListener("click", () => { sessionStorage.removeItem("dbviewer.list"); location.href = "/list"; });
  loadTables();
}
