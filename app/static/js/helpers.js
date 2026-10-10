/*
  helpers.js — 補助入力のダイアログ(VBA SF / NumFm / CF の置き換え)

  一覧のレコード編集(record.js)とマスタ管理(master.js)が同じものを使う。
  欄の決まり(kind / master / ms_join / num / date_limit …)はサーバが返した欄の辞書。

    const helpers = createHelpers({ listSql: () => "...", parent: "MF1", onMessage: toast });
    const result = await helpers.open(field, inputElement);   // {value, link?} か undefined
*/
import { api } from "./api.js";
import { toast, toastError } from "./toast.js";
import { makeSortable } from "./sort_table.js";

export function isNumericVba(v) {
  const s = String(v).normalize("NFKC").replace(/,/g, "").trim();
  return s !== "" && /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(s);
}

export function isDateVba(v) { return !Number.isNaN(new Date(String(v).replace(/-/g, "/")).getTime()) && /\d/.test(v); }

export function createHelpers({ listSql = () => "", parent = "MF1" } = {}) {
  const el = {};
  for (const [key, id] of [["dialog", "helper"], ["title", "helperTitle"], ["note", "helperNote"],
                           ["body", "helperBody"], ["ok", "helperOk"], ["clear", "helperClear"]]) {
    el[key] = document.getElementById(id);
  }
  let resolve = null;
  let getValue = null;

  function openDialog(title, note, body, fn) {
    el.title.textContent = title; el.note.textContent = note || "";
    el.body.replaceChildren(body);
    getValue = fn;
    return new Promise((r) => { resolve = r; el.dialog.showModal(); });
  }
  function close(result) { const r = resolve; resolve = null; el.dialog.close(); if (r) r(result); }

  if (el.dialog && el.dialog.dataset.wired !== "1") {
    el.dialog.dataset.wired = "1";
    el.ok.addEventListener("click", () => { const v = getValue ? getValue() : undefined; if (v === undefined) { toast("選んでください。", "info"); return; } close(v); });
    el.clear.addEventListener("click", () => close({ value: "" }));
    el.dialog.addEventListener("close", () => { if (resolve) { const r = resolve; resolve = null; r(undefined); } });
  }


  async function open(f, inp) {
  let result;
  if (f.kind === "master") result = await masterHelper(f, inp);
  else if (f.kind === "field") result = await listHelper(f, inp, "field", "フィールド", "いま一覧に出ている列名");
  else if (f.kind === "folder") result = await listHelper(f, inp, "folder", "フォルダ", "参照形式のフォルダにあるファイル");
  else if (f.kind === "num") result = await numHelper(f, inp);
  else if (f.kind === "calender") result = await calendarHelper(f, inp);
  else if (f.kind === "form") { toast(`専用フォーム「${f.form_name}」は Web 版では未対応です(このシステムでは使いません)。`, "warn"); return; }
  else return;
  return result;
  }

async function masterHelper(f, inp) {
  const link = f.ms_join && f.ms_join.includes("/");
  let data;
  try {
    data = await api.post("/api/record/choices", link ? { kind: "link", ms_join: f.ms_join, multi: f.multi }
                                                    : { kind: "master", name: f.master, multi: f.multi, parent });
  } catch (err) { toastError(err, "warn"); return undefined; }
  const wrap = document.createElement("div"); wrap.className = "tablewrap";
  const table = document.createElement("table"); table.className = "table";
  const thead = document.createElement("thead"); const hr = document.createElement("tr");
  for (const c of data.columns) { const th = document.createElement("th"); th.textContent = c; th.style.textAlign = "left"; hr.appendChild(th); }
  thead.appendChild(hr); table.appendChild(thead);
  const tbody = document.createElement("tbody");
  const current = new Set(String(inp.value).split(","));
  for (const row of data.rows) {
    const tr = document.createElement("tr");
    if (current.has(row[data.value_col - 1])) tr.setAttribute("aria-selected", "true");
    row.forEach((v) => { const td = document.createElement("td"); td.textContent = v; td.style.textAlign = "left"; tr.appendChild(td); });
    tr.addEventListener("click", (event) => {
      if (!f.multi || !(event.ctrlKey || event.metaKey)) for (const r of tbody.rows) if (r !== tr) r.removeAttribute("aria-selected");
      tr.setAttribute("aria-selected", tr.getAttribute("aria-selected") === "true" && f.multi ? "false" : "true");
    });
    tr.addEventListener("dblclick", () => close(getValue()));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody); wrap.appendChild(table);
  // 列名を押すと並べ替え(マスタ管理・一覧と同じ決まり)。選んだ行はそのまま
  makeSortable(table);
  return openDialog(`${data.name}マスタ`, (f.multi ? "Ctrl+クリックで複数選択できます。" : "ダブルクリックでも選択できます。") + "列名を押すと並べ替えます。", wrap, () => {
    const picked = [...tbody.rows].filter((r) => r.getAttribute("aria-selected") === "true");
    if (!picked.length) return undefined;
    const value = picked.map((r) => r.cells[data.value_col - 1].textContent).join(",");
    const out = { value };
    if (link && data.link_col) out.link = picked.map((r) => r.cells[data.link_col - 1].textContent).join(",");
    return out;
  });
}

async function listHelper(f, inp, kind, title, note) {
  let data;
  try {
    data = await api.post("/api/record/choices", kind === "field" ? { kind, list_sql: listSql() } : { kind, dsp_type: f.dsp_type });
  } catch (err) { toastError(err, "warn"); return undefined; }
  const box = document.createElement("div"); box.className = "helper__list";
  for (const item of data.items) {
    const d = document.createElement("div"); d.textContent = item;
    if (item === inp.value) d.setAttribute("aria-selected", "true");
    d.addEventListener("click", () => { for (const x of box.children) x.removeAttribute("aria-selected"); d.setAttribute("aria-selected", "true"); });
    d.addEventListener("dblclick", () => close({ value: item }));
    box.appendChild(d);
  }
  return openDialog(title, data.directory ? `フォルダ: ${data.directory}` : note, box, () => {
    const p = box.querySelector('[aria-selected="true"]'); return p ? { value: p.textContent } : undefined;
  });
}

async function numHelper(f, inp) {
  const { min, max, step, mode } = f.num;
  const box = document.createElement("div"); box.className = "field";
  const n = document.createElement("input"); n.type = "number"; n.className = "input"; n.min = min; n.max = max; n.step = step || 1;
  n.value = clampNum(inp.value, min, max); n.style.width = "8em";
  const r = document.createElement("input"); r.type = "range"; r.min = min; r.max = max; r.step = step || 1; r.value = n.value; r.style.flex = "1";
  n.addEventListener("input", () => { r.value = n.value; }); r.addEventListener("input", () => { n.value = r.value; });
  box.append(n, r);
  return openDialog(`数値入力(形式 ${mode})`, `${min} 〜 ${max}、刻み ${step}`, box, () => {
    // VBA BENT_Click: Ceiling(値, 刻み)。範囲外は最大/最小に直して知らせる
    let v = n.value === "" ? Number(String(inp.value).replace(/,/g, "")) || 0 : Number(n.value);
    if (step > 0) v = ceilToStep(v, step);
    if (v > max) { toast(`最大値 ${max} を超えているので、最大値に変換されました。`, "info"); v = max; }
    if (v < min) { toast(`最小値 ${min} を超えているので、最小値に変換されました。`, "info"); v = min; }
    return { value: String(v) };
  });
}

function clampNum(v, min, max) {
  const x = Number(String(v ?? "").replace(/,/g, "")) || 0;
  return Math.min(Math.max(x, min), max);
}

/** 小数点以下の桁数(1e-7 のような書き方も)。 */
function decimals(x) {
  const s = String(x).toLowerCase();
  const [m, e] = s.split("e");
  const d = (m.split(".")[1] || "").length - (e ? Number(e) : 0);
  return Math.max(0, d);
}

/**
 * 刻みの倍数に切り上げる(VBA の Ceiling)。**10 進の桁で計算する。**
 *
 * `Math.ceil(v / step) * step` は 2 進の誤差で、刻み 0.01 のとき 0.07 が 0.08 に・1.11 が 1.12 に、
 * 刻み 0.1 のとき 0.3 が 0.30000000000000004 になって本体に書かれていた(違う数値が入る)。
 * 桁をそろえた整数で割り算し、刻みの桁で書く。
 */
export function ceilToStep(v, step) {
  const d = Math.min(12, Math.max(decimals(step), decimals(v)));
  const k = 10 ** d;
  const vi = Math.round(v * k);
  const si = Math.round(step * k);
  if (!si) return v;
  const q = Math.ceil(vi / si) * si;
  return Number((q / k).toFixed(d));
}

async function calendarHelper(f, inp) {
  const box = document.createElement("div"); box.className = "field";
  const d = document.createElement("input"); d.type = "date"; d.className = "input";
  const cur = new Date(String(inp.value).replace(/-/g, "/"));
  if (inp.value && !Number.isNaN(cur.getTime())) d.value = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, "0")}-${String(cur.getDate()).padStart(2, "0")}`;
  box.appendChild(d);
  const limitNote = f.date_limit ? `${f.date_limit}しか入力できません。` : "";
  return openDialog("カレンダー", limitNote, box, () => {
    if (!d.value) return undefined;
    const [y, m, dd] = d.value.split("-");
    const text = `${y}/${m}/${dd}`;
    const picked = new Date(`${y}/${m}/${dd}`); const now = new Date();
    if ((f.date_limit === "過去" && picked > now) || (f.date_limit === "未来" && picked < now)) {
      toast(`${f.date_limit}しか入力できません。入力がキャンセルされます。`, "warn");
      return { value: inp.value };
    }
    return { value: text };
  });
}


  return { open, close };
}
