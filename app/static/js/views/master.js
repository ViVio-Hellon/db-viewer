/* マスタメンテナンス(VBA MF2)。判断はサーバ。 */
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { createHelpers, isNumericVba } from "../helpers.js";
import { makeSortable } from "../sort_table.js";

const el = {};
const state = { table: "", columns: [], rows: [], selected: -1, mode: "", fields: [], readOnly: false };
let helpers = null;
let sorter = null;
let sorterTable = "";

function problem(text) { el.msProblem.hidden = !text; el.msProblem.textContent = text || ""; }

async function loadTables() {
  try {
    const d = await api.post("/api/master/tables", {});
    el.msTitle.textContent = d.title;
    const brought = new Set(d.brought || []);
    if (!d.tables.length) problem(`このレベル(${d.level})で直せる表はありません。マスタ管理は Level 3 以上です。`);
    el.msTables.replaceChildren(...d.tables.map((t) => {
      const div = document.createElement("div"); div.textContent = t;
      // なぜこの表が出ているか(いま使っている表だけを出している)
      if (d.why && d.why[t]) {
        const why = document.createElement("small"); why.className = "ms__why"; why.textContent = d.why[t];
        div.appendChild(why);
      }
      // 「表を持ってくる」で足した表(設定 → Access 取り込み)
      if (brought.has(t)) {
        const mark = document.createElement("span");
        mark.className = "badge"; mark.textContent = "足"; mark.title = "設定の「表を持ってくる」で足した表";
        mark.style.marginLeft = "6px";
        div.appendChild(mark);
      }
      div.addEventListener("click", () => selectTable(t, div));
      return div;
    }));
  } catch (err) { if (err.status === 401) location.href = "/login?next=/master"; else toastError(err); }
}

async function selectTable(t, node) {
  for (const x of el.msTables.children) x.removeAttribute("aria-selected");
  node.setAttribute("aria-selected", "true");
  state.table = t; state.selected = -1;
  switchScreen(0);
  await loadRows();
}

async function loadRows() {
  try {
    const d = await api.post("/api/master/rows", { table: state.table });
    state.columns = d.columns; state.rows = d.rows; state.readOnly = d.read_only;
    el.msName.textContent = d.table; el.msPage.textContent = `${d.page} (${d.count} 件)`;
    renderTable();
    // 履歴(H_*)は見るだけ
    el.msNew.hidden = el.msEdit.hidden = el.msDelete.hidden = d.read_only;
  } catch (err) { toastError(err); }
}

function selectRow(i) {
  state.selected = i;
  for (const r of el.msTable.tBodies[0].rows) {
    if (Number(r.dataset.i) === i) r.setAttribute("aria-selected", "true"); else r.removeAttribute("aria-selected");
  }
}

function renderTable() {
  const thead = el.msTable.tHead; const tbody = el.msTable.tBodies[0];
  const tr = document.createElement("tr");
  for (const c of state.columns) { const th = document.createElement("th"); th.textContent = c; th.className = "l"; tr.appendChild(th); }
  thead.replaceChildren(tr);
  // 行には元の番号(state.rows の何番目か)を持たせる。並べ替えても選んだ行はずれない
  tbody.replaceChildren(...state.rows.map((row, i) => {
    const r = document.createElement("tr");
    r.dataset.i = String(i);
    if (i === state.selected) r.setAttribute("aria-selected", "true");
    row.forEach((v) => { const td = document.createElement("td"); td.textContent = v; td.className = "l"; r.appendChild(td); });
    r.addEventListener("click", () => selectRow(i));
    r.addEventListener("dblclick", () => { selectRow(i); if (!state.readOnly) openForm("UPDATE"); });
    return r;
  }));
  // 列名を押すと並べ替え。並べ替えは表ごとに覚える(直して戻っても同じ並び)
  if (!sorter || sorterTable !== state.table) {
    sorter = makeSortable(el.msTable, { stateKey: `dbviewer.master.sort.${state.table}` });
    sorterTable = state.table;
  } else {
    sorter.apply();
  }
}

function switchScreen(mode) {
  // VBA 画面スイッチ: 0 = 表の一覧、1 = 編集
  const edit = mode === 1;
  el.msGrid.hidden = edit; el.msFields.hidden = !edit;
  el.msNew.hidden = edit || state.readOnly || !state.table; el.msEdit.hidden = edit || state.readOnly || !state.table;
  el.msDelete.hidden = edit || state.readOnly || !state.table;
  el.msBack.hidden = !edit; el.msExe.hidden = !edit;
}

async function openForm(mode) {
  if (!state.table) return;
  if (mode === "UPDATE" && state.selected < 0) { toast("修正対象データがありません。", "info"); return; }
  try {
    const d = await api.post("/api/master/form", { table: state.table, mode, row: mode === "UPDATE" ? state.rows[state.selected] : null });
    state.mode = mode; state.fields = d.fields;
    renderFields();
    switchScreen(1);
  } catch (err) { toastError(err); }
}

function renderFields() {
  const rows = [];
  for (const f of state.fields) {
    const row = document.createElement("div"); row.className = "rec__row";
    const lbl = document.createElement("div"); lbl.className = `rec__lbl lbl--${f.color}`; lbl.textContent = f.name; lbl.title = f.input_type || "";
    const val = document.createElement("div"); val.className = "rec__val";
    const inp = document.createElement(f.lines > 1 ? "textarea" : "input"); inp.className = "input"; inp.value = f.value; inp.dataset.no = f.no;
    if (f.is_key || f.kind === "auto" || f.kind === "view") { inp.readOnly = true; inp.disabled = f.kind !== "free" && !f.is_key ? true : f.is_key; inp.classList.add("is-auto"); }
    else if (f.locked) { inp.readOnly = true; inp.title = "ダブルクリックか「…」で入力"; }
    if (f.kind === "error") { inp.value = "SYSTEM ERROR"; inp.readOnly = true; toast(f.message, "warn"); }
    inp.addEventListener("input", () => inp.classList.toggle("is-changed", inp.value !== f.original));
    inp.addEventListener("dblclick", () => helper(f, inp));
    inp.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && f.kind === "numconv") { event.preventDefault(); inp.value = isNumericVba(inp.value) ? inp.value.normalize("NFKC") : (inp.value === "" ? "" : "0"); inp.classList.toggle("is-changed", inp.value !== f.original); }
      if ((event.key === "Delete" || event.key === "Backspace") && inp.readOnly && !f.is_key) {
        event.preventDefault();
        if (f.kind === "auto" || f.kind === "view") { toast("編集禁止項目です。", "info"); return; }
        if (confirm("入力制限がある項目です。削除しますか?")) { inp.value = ""; inp.classList.toggle("is-changed", inp.value !== f.original); }
      }
    });
    val.appendChild(inp);
    if (f.locked && !f.is_key && !["auto", "view", "error"].includes(f.kind)) {
      const b = document.createElement("button"); b.type = "button"; b.className = "btn btn--find"; b.textContent = "…"; b.addEventListener("click", () => helper(f, inp)); val.appendChild(b);
    }
    row.append(lbl, val); rows.push(row);
  }
  el.msFields.style.gridTemplateRows = `repeat(${Math.ceil(rows.length / 2)}, auto)`;
  el.msFields.style.gridAutoFlow = "column";
  el.msFields.replaceChildren(...rows);
}

async function helper(f, inp) {
  if (f.is_key || !f.locked || ["auto", "view", "error"].includes(f.kind)) return;
  const result = await helpers.open(f, inp);
  if (result === undefined) return;
  inp.value = result.value;
  inp.classList.toggle("is-changed", inp.value !== f.original);
}

function collect() {
  return state.fields.map((f) => { const inp = el.msFields.querySelector(`[data-no="${f.no}"]`); return inp ? inp.value : f.value; });
}

async function execute() {
  const values = collect();
  const key = values[0];
  const seen = state.mode === "UPDATE" ? state.rows[state.selected] : null;
  const base = { table: state.table, mode: state.mode, values, key, seen };
  try {
    let res = await api.post("/api/master/execute", { ...base, confirmed: false });
    if (res.reason === "question") {
      const yes = confirm(res.message);
      res = await api.post("/api/master/execute", { ...base, confirmed: true, answer: yes });
    }
    toast(res.message, res.ok ? "ok" : "warn");
    if (res.ok) { switchScreen(0); await loadRows(); }
  } catch (err) { toastError(err, err.status === 409 ? "error" : "warn"); }
}

async function remove() {
  if (state.selected < 0) { toast("削除対象データがありません。", "info"); return; }
  const row = state.rows[state.selected];
  if (!confirm("削除しますか？")) { toast("キャンセルしました。", "info"); return; }
  try {
    const res = await api.post("/api/master/delete", { table: state.table, row, confirmed: true, answer: true });
    toast(res.message, res.ok ? "warn" : "warn");
    state.selected = -1;
    await loadRows();
  } catch (err) { toastError(err); }
}

export function start() {
  for (const id of ["msTitle", "msProblem", "msTables", "msName", "msPage", "msNew", "msEdit", "msDelete", "msBack", "msExe", "msClose", "msGrid", "msTable", "msFields"]) el[id] = document.getElementById(id);
  helpers = createHelpers({ parent: "MF2" });
  el.msNew.addEventListener("click", () => openForm("ADDNEW"));
  el.msEdit.addEventListener("click", () => openForm("UPDATE"));
  el.msDelete.addEventListener("click", remove);
  el.msBack.addEventListener("click", () => switchScreen(0));
  el.msExe.addEventListener("click", execute);
  // VBA MF2.QueryClose: 一覧を作り直してフィルタを消す
  el.msClose.addEventListener("click", () => { location.href = "/list"; });
  switchScreen(0);
  loadTables();
}
