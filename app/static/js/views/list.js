/*
  一覧。VBA の表示(フィルタ列 + ListView + ページ)は捨て、Web の表にした。

  - 全行をまとめて受け取り、絞り込み・並べ替え・列の設定は画面の中で(grid.js)
  - 右に選んだ行の詳細(隠した列も含めて全部)
  - 帳票・印刷・コピー・保存は「いま見えているとおり」
  判断(誰が何をできるか・登録の決まり)は今までどおりサーバ(レコード画面)。
*/
import { api, tokenUrl } from "../api.js";
import { toast, toastError } from "../toast.js";
import { createGrid } from "../grid.js";
import * as out from "../export_ui.js";

const el = {};
const DETAIL_KEY = "dbviewer.list.detail";
let grid = null;
let data = null;            // サーバから受け取った一覧({columns, rows, key_index, setup, ...})
let warned = false;

function problem(text) {
  el.listProblem.hidden = !text;
  el.listProblem.textContent = text || "";
}

/* ---------------- 読む ---------------- */
async function load() {
  try {
    data = await api.post("/api/list/data", {});
    problem("");
    el.listUpdated.hidden = true;
    el.listTitle.textContent = data.setup.main_table || "一覧";
    el.actReport.hidden = !data.setup.report_flag;
    grid.setData({ columns: data.columns, rows: data.rows, keyIndex: data.key_index, view: data.view });
    if (data.setup.column_problems && data.setup.column_problems.length && !warned) {
      warned = true;
      toast(data.setup.column_problems.join("\n"), "warn");
    }
  } catch (err) {
    if (err.status === 401) { location.href = "/login?next=/list"; return; }
    if (err.status === 409 || err.status === 422) problem(err.message);
    else toastError(err);
  }
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
  small.textContent = data.key_index >= 0 ? `${names[data.key_index]} ${keyOf(row)}` : "選んだ行";
  const title = document.createElement("b");
  const firstText = shown.find((n) => n !== names[data.key_index] && (row[idx[n]] || "").length > 1);
  title.textContent = firstText ? row[idx[firstText]] : "";
  head.append(small, title);
  const dl = document.createElement("dl");
  dl.className = "dt__list";
  const add = (n, cls = "") => {
    const dt = document.createElement("dt"); dt.textContent = n; if (cls) dt.className = cls;
    const dd = document.createElement("dd"); dd.textContent = row[idx[n]] ?? ""; if (cls) dd.className = cls;
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
  const q = new URLSearchParams({ mode, click });
  if (mode !== "新規") q.set("key", keyOf(row));
  location.href = `/record?${q}`;
}

/* ---------------- 見えているとおりに取り出す ---------------- */
function visibleTable() {
  const names = data.columns.map((c) => c.name);
  const cols = grid.visibleColumns();
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
    const res = await api.post("/api/report/run", { keys });
    window.open(tokenUrl(res.preview), "_blank", "noopener");
  } catch (err) { toastError(err); }
}

export function start() {
  for (const id of ["listTitle", "listProblem", "listUpdated", "listReload", "lvBody", "grid", "detail",
                    "actNew", "actEdit", "actView", "actDelete", "actReport", "actPrint", "actOut",
                    "outMenu", "outCopy", "outSave", "actDetail"]) {
    el[id] = document.getElementById(id);
  }
  grid = createGrid(el.grid, {
    stateKey: "dbviewer.list.grid",
    onSelect,
    onOpen: () => command("修正", "DblClick"),
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

  el.actNew.addEventListener("click", () => command("新規"));
  el.actEdit.addEventListener("click", () => command("修正"));
  el.actView.addEventListener("click", () => command("参照"));
  el.actDelete.addEventListener("click", () => command("削除"));
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
  el.listReload.addEventListener("click", load);
  document.addEventListener("app:health", onHealth);
  load();
}
