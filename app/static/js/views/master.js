/* マスタメンテナンス(VBA MF2)。判断はサーバ。 */
import { api } from "../api.js";
import { makeField, inputOf, previewFile } from "../fieldinput.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";
import { createHelpers, isNumericVba } from "../helpers.js";
import { makeSortable } from "../sort_table.js";
import { isActive, actionHint, runAction } from "../cellaction.js";

const el = {};
const state = { table: "", columns: [], rows: [], selected: -1, mode: "", fields: [], readOnly: false,
                caps: { rows: false, columns: false, note: "" }, kinds: [], newColumn: "", actions: {} };
// 表の名前 → 左の一覧の要素(列を足したあと、設定の表へ移るため)
const tableNodes = new Map();
let helpers = null;
let sorter = null;
let sorterTable = "";

function problem(text) { el.msProblem.hidden = !text; el.msProblem.textContent = text || ""; }

async function loadTables() {
  try {
    const d = await api.post("/api/master/tables", {});
    el.msTitle.textContent = d.title;
    const brought = new Set(d.brought || []);
    if (!d.tables.length) problem("見られる表がありません。");
    tableNodes.clear();
    el.msTables.replaceChildren(...d.tables.map((t) => {
      const div = document.createElement("div"); div.textContent = t;
      tableNodes.set(t, div);
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
  state.table = t; state.selected = -1; state.newColumn = "";
  switchScreen(0);
  await loadRows();
}

/**
 * 表を読み直す。`selectKey` があれば、1 列目がその値の行を選んで見えるところへ。
 * `selectNew` なら、読み直す前に無かった行(いま足した行)を選ぶ。
 */
async function loadRows({ selectKey = null, selectNew = false } = {}) {
  const before = new Set(state.rows.map((r) => r[0]));
  try {
    const d = await api.post("/api/master/rows", { table: state.table });
    state.columns = d.columns; state.rows = d.rows; state.readOnly = d.read_only;
    state.caps = d.caps || { rows: !d.read_only, columns: false, note: "" };
    state.kinds = d.kinds || [];
    state.actions = d.actions || {};
    el.msName.textContent = d.table; el.msPage.textContent = `${d.page} (${d.count} 件)`;
    let pick = -1;
    if (selectKey !== null) pick = state.rows.findIndex((r) => String(r[0]) === String(selectKey));
    else if (selectNew) pick = state.rows.findIndex((r) => !before.has(r[0]));
    if (pick >= 0) state.selected = pick;
    renderTable();
    showCaps();
    if (pick >= 0) el.msTable.querySelector(`tbody tr[data-i="${pick}"]`)?.scrollIntoView({ block: "center" });
  } catch (err) { toastError(err); }
}

/** その表でできること(サーバが決めた)をボタンとひとことに写す。 */
function showCaps() {
  const editing = !el.msFields.hidden;
  el.msNew.hidden = el.msEdit.hidden = el.msDelete.hidden = editing || !state.table || !state.caps.rows;
  el.msAddCol.hidden = editing || !state.table || !state.caps.columns;
  el.msCapNote.hidden = !state.caps.note;
  el.msCapNote.textContent = state.caps.note || "";
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
  const fresh = state.columns.indexOf(state.newColumn);
  for (const c of state.columns) {
    const th = document.createElement("th"); th.textContent = c; th.className = "l";
    if (c === state.newColumn) th.classList.add("is-new");
    tr.appendChild(th);
  }
  thead.replaceChildren(tr);
  // 行には元の番号(state.rows の何番目か)を持たせる。並べ替えても選んだ行はずれない
  tbody.replaceChildren(...state.rows.map((row, i) => {
    const r = document.createElement("tr");
    r.dataset.i = String(i);
    if (i === state.selected) r.setAttribute("aria-selected", "true");
    row.forEach((v, j) => {
      const td = document.createElement("td"); td.className = j === fresh ? "l is-new" : "l";
      const act = state.actions[state.columns[j]];
      if (isActive(act, v)) {
        // かんたん設定の「クリック」: 値を 1 回押すとコピー・URL を開く(ダブルクリックで 2 回動かさない)
        const span = document.createElement("span");
        span.className = `cell-act cell-act--${act.click}`; span.textContent = v; span.title = actionHint(act, v);
        span.addEventListener("click", (e) => { if (e.detail <= 1) runAction(act, v); });
        span.addEventListener("dblclick", (e) => e.stopPropagation());
        span.addEventListener("mousedown", (e) => { if (e.detail > 1) e.preventDefault(); });
        td.appendChild(span);
      } else {
        td.textContent = v;
      }
      r.appendChild(td);
    });
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
  el.msBack.hidden = !edit; el.msExe.hidden = !edit;
  showCaps();
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
    if (f.simple) {
      // かんたん設定で決めた入力(コンボボックス・スピンボタン・カレンダー)
      const field = makeField(f.simple, f.value, { onPreview: (v) => previewFile(state.table, f.name, v) });
      const si = inputOf(field); si.dataset.no = f.no;
      si.addEventListener("input", () => si.classList.toggle("is-changed", si.value !== f.original));
      val.appendChild(field);
      row.append(lbl, val); rows.push(row);
      continue;
    }
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
        confirmBox("入力制限がある項目です。削除しますか?", { ok: "削除する", danger: true }).then((yes) => { if (yes) { inp.value = ""; inp.classList.toggle("is-changed", inp.value !== f.original); } });
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
      const yes = await confirmBox(res.message, { ok: "はい", cancel: "いいえ" });
      res = await api.post("/api/master/execute", { ...base, confirmed: true, answer: yes });
    }
    toast(res.message, res.ok ? "ok" : "warn");
    // 足した行は選んで見えるところへ(どこに入ったか探させない)
    if (res.ok) { switchScreen(0); await loadRows({ selectNew: state.mode === "ADDNEW" }); }
  } catch (err) { toastError(err, err.status === 409 ? "error" : "warn"); }
}

/* ---------------- 列を追加 ---------------- */
function openAddColumn() {
  if (!state.table || !state.caps.columns) return;
  el.colTitle.textContent = `[${state.table}] に列を追加`;
  el.colName.value = ""; el.colFill.value = "";
  el.colKinds.replaceChildren(...state.kinds.map(([k, label]) => {
    const lab = document.createElement("label"); lab.className = "checkline";
    const r = document.createElement("input"); r.type = "radio"; r.name = "colKind"; r.value = k; r.checked = k === "text";
    lab.append(r, document.createTextNode(` ${label}`));
    return lab;
  }));
  const notes = ["いちばん右に足します。いまある行は空(または下の値)になります。",
                 "足す前に本体の控えを取ります(設定 → この端末 のローカル領域の backup)。"];
  if (state.caps.setting_table) {
    notes.unshift(`[${state.caps.setting_table}] にも同じ名前の行を足します。入力形式などは、あとでその行を直して決めます。`);
  }
  el.colNotes.replaceChildren(...notes.map((t) => { const li = document.createElement("li"); li.textContent = t; return li; }));
  el.colProblem.hidden = true;
  el.colDialog.showModal();
  el.colName.focus();
}

async function runAddColumn() {
  const body = {
    table: state.table, name: el.colName.value,
    kind: el.colKinds.querySelector("input:checked")?.value || "text", fill: el.colFill.value,
  };
  try {
    const res = await api.post("/api/master/add-column", body);
    el.colDialog.close();
    state.newColumn = res.column;
    await loadRows();
    // 足した列が見えるところへ(いちばん右)
    el.msGrid.scrollLeft = el.msGrid.scrollWidth;
    const conv = res.setting_table;
    toast(res.message, "ok", conv && tableNodes.has(conv)
      ? { label: "設定の行を直す", onClick: () => openSettingRow(conv, res.setting_key) } : null);
  } catch (err) {
    // 断り(名前が重なる等)はダイアログの中に出す。直してもう一度押せるように
    if (err.status && err.status < 500) { el.colProblem.hidden = false; el.colProblem.textContent = err.message; }
    else toastError(err);
  }
}

/** 列と一緒に足した設定の行を開く(入力形式などを決めるため)。 */
async function openSettingRow(conv, key) {
  const node = tableNodes.get(conv);
  if (!node) return;
  for (const x of el.msTables.children) x.removeAttribute("aria-selected");
  node.setAttribute("aria-selected", "true");
  state.table = conv; state.selected = -1; state.newColumn = "";
  switchScreen(0);
  await loadRows({ selectKey: key });
  if (state.selected >= 0 && state.caps.rows) openForm("UPDATE");
}

async function remove() {
  if (state.selected < 0) { toast("削除対象データがありません。", "info"); return; }
  const row = state.rows[state.selected];
  if (!(await confirmBox("削除しますか？", { ok: "削除する", danger: true }))) { toast("キャンセルしました。", "info"); return; }
  try {
    const res = await api.post("/api/master/delete", { table: state.table, row, confirmed: true, answer: true });
    toast(res.message, res.ok ? "warn" : "warn");
    state.selected = -1;
    await loadRows();
  } catch (err) { toastError(err, "warn"); }
}

export function start() {
  for (const id of ["msTitle", "msProblem", "msTables", "msName", "msPage", "msNew", "msEdit", "msDelete", "msBack",
                    "msExe", "msClose", "msGrid", "msTable", "msFields", "msAddCol", "msCapNote",
                    "colDialog", "colTitle", "colName", "colKinds", "colFill", "colNotes", "colProblem", "colRun"]) {
    el[id] = document.getElementById(id);
  }
  helpers = createHelpers({ parent: "MF2" });
  el.msNew.addEventListener("click", () => openForm("ADDNEW"));
  el.msEdit.addEventListener("click", () => openForm("UPDATE"));
  el.msDelete.addEventListener("click", remove);
  el.msAddCol.addEventListener("click", openAddColumn);
  el.colRun.addEventListener("click", runAddColumn);
  el.colName.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runAddColumn(); } });
  el.colFill.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runAddColumn(); } });
  el.msBack.addEventListener("click", () => switchScreen(0));
  el.msExe.addEventListener("click", execute);
  // VBA MF2.QueryClose: 一覧を作り直してフィルタを消す
  el.msClose.addEventListener("click", () => { location.href = "/list"; });
  switchScreen(0);
  loadTables();
}
