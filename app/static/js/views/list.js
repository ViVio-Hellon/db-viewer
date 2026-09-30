/*
  一覧(VBA MF1 の一覧ページ)。

  判断はサーバが済ませる。ここが持つのは「いまどのフィルタを選んでいるか」
  「何ページ目か」だけ(VBA の FL/FLL/SB1 にあたる)。タブを 2 枚開いても
  それぞれが自分の状態を持つだけで、取り合うものは無い。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";

const el = {};
const state = {
  filters: {},        // {no: 値}
  candidates: {},     // {no: [..]}(サーバが null を返した列は前のまま)
  page: 1,
  perPage: 24,
  mode: "選択",
  columns: [],
  rows: [],
  keyCol: 0,
  selected: null,     // 選んだ行の 管理番号
  setup: {},
};

const STORE = "dbviewer.list";
function remember() {
  try { sessionStorage.setItem(STORE, JSON.stringify({ filters: state.filters, page: state.page, perPage: state.perPage, sql: el.sqlBox.textContent, selected: state.selected })); } catch (e) { /* 覚えられないだけ */ }
}
function recall() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE) || "null");
    if (!saved) return;
    state.filters = saved.filters || {}; state.page = saved.page || 1; state.perPage = saved.perPage || 24; state.selected = saved.selected || null;
    el.perPage.value = String(state.perPage);
  } catch (e) { /* 壊れていれば最初から */ }
}

function problem(text) {
  el.listProblem.hidden = !text;
  el.listProblem.textContent = text || "";
}

async function load({ eventNo = 0, pageFlag = false, reset = false } = {}) {
  try {
    const view = await api.post("/api/list", {
      filters: state.filters, page: state.page, per_page: state.perPage,
      page_flag: pageFlag, event_no: eventNo, reset,
    });
    problem("");
    if (reset) state.filters = {};
    render(view);
  } catch (err) {
    if (err.status === 401) { location.href = "/login?next=/list"; return; }
    if (err.status === 409 || err.status === 422) problem(err.message);
    else toastError(err);
  }
}

function render(view) {
  state.mode = view.mode || "選択";
  state.columns = view.columns;
  state.rows = view.rows;
  state.keyCol = view.key_item_col || 0;
  state.setup = view.setup || state.setup;
  state.page = view.page.no;
  el.modeChip.textContent = state.mode;
  el.pageLabel.textContent = view.page.label;
  el.rowCount.textContent = `${view.page.rows} 件`;
  el.pagePrev.disabled = view.page.no <= 1;
  el.pageNext.disabled = view.page.no >= view.page.count;
  el.sqlBox.textContent = view.sql || "";
  renderFilters(view.filters || []);
  renderTable(view.columns, view.rows, view.align || []);
  remember();
  el.btnReport.hidden = !(state.setup.report_flag);
  el.btnBrowse.hidden = !(state.setup.browse_flag);
  if (state.setup.column_problems && state.setup.column_problems.length && !state.warned) {
    state.warned = true;
    toast(state.setup.column_problems.join("\n"), "warn");
  }
}

function renderFilters(filters) {
  const cols = [];
  for (const f of filters) {
    if (f.candidates !== null && f.candidates !== undefined) state.candidates[f.no] = f.candidates;
    const cands = state.candidates[f.no] || [];
    const col = document.createElement("div");
    col.className = "flt__col";
    col.style.flexBasis = `${f.width_pct}%`;
    const head = document.createElement("div");
    head.className = "flt__head" + (f.kind === "master" ? " flt__head--master" : "");
    head.textContent = f.label;
    head.title = (f.tip ? f.tip + " / " : "") + "ダブルクリックで集計";
    head.dataset.item = f.item;
    head.addEventListener("dblclick", () => summary(f.item));
    const sel = document.createElement("div");
    sel.className = "flt__sel";
    sel.textContent = f.value || "";
    const list = document.createElement("div");
    list.className = "flt__list";
    // VBA の ListBox: 先頭に空欄(解除)、候補、末尾に空欄ダミー
    for (const value of ["", ...cands, ""]) {
      const item = document.createElement("div");
      item.textContent = value;
      if (value === (f.value || "") && value !== "") item.setAttribute("aria-selected", "true");
      item.addEventListener("click", () => pick(f.no, value));
      list.appendChild(item);
    }
    col.append(head, sel, list);
    cols.push(col);
  }
  el.filters.replaceChildren(...cols);
}

function renderTable(columns, rows, align) {
  const thead = el.listTable.tHead;
  const tbody = el.listTable.tBodies[0];
  const tr = document.createElement("tr");
  columns.forEach((c, j) => {
    const th = document.createElement("th");
    th.textContent = c;
    th.className = (align[j] || "left")[0];
    tr.appendChild(th);
  });
  thead.replaceChildren(tr);
  const out = [];
  for (const row of rows) {
    const r = document.createElement("tr");
    const key = state.keyCol ? row[state.keyCol - 1] : "";
    r.dataset.key = key;
    if (key !== "" && key === state.selected) r.setAttribute("aria-selected", "true");
    row.forEach((v, j) => {
      const td = document.createElement("td");
      td.textContent = v;
      td.className = (align[j] || "left")[0];
      r.appendChild(td);
    });
    r.addEventListener("click", () => select(r));
    r.addEventListener("dblclick", () => { select(r); command("修正", "DblClick"); });
    out.push(r);
  }
  tbody.replaceChildren(...out);
}

function select(tr) {
  for (const r of el.listTable.tBodies[0].rows) r.removeAttribute("aria-selected");
  tr.setAttribute("aria-selected", "true");
  state.selected = tr.dataset.key;
  remember();
}

function pick(no, value) {
  // VBA ListClickEvents: 選んだ値を見出し下に出し、押した列以外の候補を作り直す
  state.filters[no] = value;
  state.mode = "選択";
  state.page = 1;
  load({ eventNo: no });
}

async function summary(item) {
  try {
    const view = await api.post("/api/list/summary", { item });
    state.mode = "集計";
    el.modeChip.textContent = "集計";
    el.sqlBox.textContent = view.sql;
    el.pageLabel.textContent = view.page.label;
    el.rowCount.textContent = `${view.page.rows} 件`;
    renderTable(view.columns, view.rows, view.align);
    for (const b of el.sideButtons.querySelectorAll("button")) b.hidden = b.id !== "btnBack";
  } catch (err) { toastError(err); }
}

function leaveSummary() {
  // VBA REG_BAC: 一覧に戻る
  for (const b of el.sideButtons.querySelectorAll("button")) b.hidden = false;
  el.btnBack.hidden = true;
  state.mode = "選択";
  load();
}

function command(cmd, clickType = "ButtonClick") {
  if (state.mode === "集計") { leaveSummary(); return; }
  const key = state.selected;
  if (cmd !== "新規" && !key) { toast("対象が選択されていません。", "warn"); return; }
  const q = new URLSearchParams({ mode: cmd, click: clickType });
  if (key) q.set("key", key);
  location.href = `/record?${q}`;
}

export function start() {
  for (const id of ["filters", "listTable", "pagePrev", "pageNext", "pageLabel", "rowCount", "perPage",
                    "sqlBox", "modeChip", "listProblem", "sideButtons", "btnNew", "btnEdit", "btnDelete",
                    "btnView", "btnReport", "btnBrowse", "btnSetting", "btnOutput", "btnBack"]) {
    el[id] = document.getElementById(id);
  }
  el.pagePrev.addEventListener("click", () => { state.page -= 1; load({ pageFlag: true }); });
  el.pageNext.addEventListener("click", () => { state.page += 1; load({ pageFlag: true }); });
  el.perPage.addEventListener("change", () => { state.perPage = Number(el.perPage.value); state.page = 1; load(); });
  for (const b of [el.btnNew, el.btnEdit, el.btnDelete, el.btnView]) {
    b.addEventListener("click", () => command(b.dataset.cmd));
  }
  el.btnBack.addEventListener("click", leaveSummary);
  el.btnSetting.addEventListener("click", () => { location.href = "/master"; });
  el.btnOutput.addEventListener("click", () => { location.href = "/browse?from=list"; });
  el.btnReport.addEventListener("click", () => { location.href = "/report"; });
  el.btnBrowse.addEventListener("click", () => { location.href = "/browse"; });
  recall();
  load({ pageFlag: true });
}
