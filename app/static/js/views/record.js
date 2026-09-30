/*
  レコードの編集画面(VBA MF1 の編集ページ)。

  欄の決まり(入力形式・必須・履歴・自動・連動・参照形式)はサーバが 1 欄ずつ返す。
  ここは並べて、補助入力(マスタ / フィールド / フォルダ / 数値 / カレンダー)を
  ダイアログで出し、変えた欄を橙にして、実行を頼むだけ。
*/
import { api, tokenUrl } from "../api.js";
import { toast, toastError } from "../toast.js";
import { createHelpers, isDateVba, isNumericVba } from "../helpers.js";

const el = {};
const state = { mode: "参照", key: "", click: "ButtonClick", fields: [], keys: [], level: 0, listSql: "" };

// VBA の色名(vb*/rgb*)→ CSS の色名
function cssColor(name) {
  const n = String(name || "").trim();
  if (/^vb/i.test(n)) return n.slice(2).toLowerCase();
  if (/^rgb/i.test(n)) return n.slice(3).toLowerCase();
  return n || "black";
}

function problem(text) { el.recProblem.hidden = !text; el.recProblem.textContent = text || ""; }

async function loadForm(mode, key) {
  try {
    const view = await api.post("/api/record/form", { mode, key, click: state.click, list_sql: state.listSql });
    state.mode = view.mode; state.key = view.key; state.fields = view.fields; state.keys = view.keys; state.level = view.level;
    problem("");
    render();
  } catch (err) {
    if (err.status === 403 || err.status === 422 || err.status === 409) {
      toast(err.message, "warn");
      history.back();
      return;
    }
    toastError(err);
  }
}

function inputFor(f) {
  const multi = f.lines > 1 || /\r?\n/.test(f.value);
  const inp = document.createElement(multi ? "textarea" : "input");
  inp.className = "input";
  if (multi) inp.rows = Math.max(f.lines, (f.value.match(/\n/g) || []).length + 1);
  inp.value = f.value;
  inp.dataset.no = f.no;
  inp.dataset.name = f.name;
  if (f.is_key || f.kind === "auto" || f.kind === "view") {
    inp.readOnly = true; inp.disabled = f.kind === "auto" || f.kind === "view";
    inp.classList.add("is-auto");
  } else if (state.mode === "参照") {
    inp.readOnly = true;
  } else if (f.locked) {
    inp.readOnly = true; inp.classList.add("is-locked");
    inp.title = "ダブルクリックか「…」で入力";
  }
  if (f.kind === "error") { inp.value = "SYSTEM ERROR"; inp.readOnly = true; }
  inp.addEventListener("input", () => markChanged(inp, f));
  inp.addEventListener("keydown", (event) => onKey(event, inp, f));
  inp.addEventListener("dblclick", () => onDblClick(inp, f));
  return inp;
}

function markChanged(inp, f) {
  if (state.mode === "参照" || f.is_key) return;
  inp.classList.remove("is-required-ng");
  // VBA TEXTChangeEvents: Tag(元の値) と違えば橙
  inp.classList.toggle("is-changed", inp.value !== f.original);
}

function onKey(event, inp, f) {
  if (state.mode === "参照") return;
  const del = event.key === "Backspace" || event.key === "Delete";
  if (event.key === "Enter" && f.kind === "numconv") {
    // VBA TextEnterEvents: 数値なら半角に、そうでなく空でなければ 0
    event.preventDefault();
    const v = inp.value;
    inp.value = isNumericVba(v) ? v.normalize("NFKC") : (v === "" ? "" : "0");
    markChanged(inp, f);
    return;
  }
  if (del && inp.readOnly && !f.is_key) {
    event.preventDefault();
    if (f.kind === "auto" || f.kind === "view") { toast("編集禁止項目です。", "info"); return; }
    if (confirm("入力制限がある項目です。削除しますか?")) { inp.value = ""; markChanged(inp, f); }
  }
}

function onDblClick(inp, f) {
  if (state.mode === "参照") {
    if (f.file_kind) openFile(f);
    return;
  }
  if (f.is_key || !f.locked || f.kind === "auto" || f.kind === "view" || f.kind === "error") return;
  helper(f, inp);
}

async function openFile(f) {
  try {
    const inp = el.recFields.querySelector(`[data-no="${f.no}"]`);
    await api.post("/api/record/open-file", { dir: f.file_dir, name: inp.value, kind: f.file_kind.toLowerCase() });
  } catch (err) { toast(err.message, "warn"); }
}

function render() {
  el.recTitle.textContent = `${state.mode}モード`;
  el.recMode.textContent = `Level ${state.level}`;
  el.btnExe.hidden = state.mode === "参照";
  el.recNav.hidden = state.mode === "新規";
  el.navKeys.replaceChildren(...state.keys.map((k) => {
    const o = document.createElement("option"); o.value = k; o.textContent = k; if (k === state.key) o.selected = true; return o;
  }));
  const rows = [];
  for (const f of state.fields) {
    const row = document.createElement("div");
    row.className = "rec__row";
    const lbl = document.createElement("div");
    lbl.className = `rec__lbl lbl--${f.color}`;
    lbl.textContent = f.name;
    lbl.title = f.input_type || "";
    const val = document.createElement("div");
    val.className = "rec__val";
    const inp = inputFor(f);
    val.appendChild(inp);
    if (f.link_display !== undefined && f.dsp_type.startsWith("連動")) {
      const j = document.createElement("span"); j.className = "rec__join"; j.id = `join-${f.no}`; j.textContent = f.link_display; val.appendChild(j);
    }
    if (state.mode !== "参照" && f.locked && !f.is_key && f.kind !== "auto" && f.kind !== "view" && f.kind !== "error") {
      const b = document.createElement("button"); b.type = "button"; b.className = "btn btn--find"; b.textContent = "…";
      b.title = "入力の補助を開く"; b.addEventListener("click", () => helper(f, inp)); val.appendChild(b);
    }
    if (f.required) { const s = document.createElement("span"); s.className = "rec__badge badge--must"; s.textContent = "必須"; s.title = "必須設定項目です。"; val.appendChild(s); }
    if (f.his_rec) { const s = document.createElement("span"); s.className = "rec__badge badge--his"; s.textContent = "履歴"; s.title = `履歴: ${f.his_rec}`; val.appendChild(s); }
    if (state.mode === "参照" && f.file_kind) {
      const b = document.createElement("button"); b.type = "button"; b.className = "btn btn--find"; b.textContent = f.file_kind;
      b.title = "ファイルを開く"; b.addEventListener("click", () => openFile(f)); val.appendChild(b);
    }
    if (state.mode === "参照" && f.icons && f.icons.length) {
      const box = document.createElement("div"); box.className = "rec__icons";
      for (const ic of f.icons) {
        if (!ic.exists) continue;
        const img = document.createElement("img"); img.src = tokenUrl(`/api/record/icon?path=${encodeURIComponent(ic.path)}`); img.title = ic.name;
        img.addEventListener("dblclick", () => api.post("/api/record/open-file", { dir: f.file_dir || "", name: ic.path, kind: "" }).catch((e) => toast(e.message, "warn")));
        box.appendChild(img);
      }
      val.appendChild(box);
    }
    if (f.kind === "error") toast(f.message, "warn");
    if (state.mode === "参照") applyColorRule(inp, f);
    row.append(lbl, val);
    rows.push(row);
  }
  // VBA は左の列を上から埋めてから右の列へ(MF1_Resize_Page2)。同じ読み順にする
  el.recFields.style.gridTemplateRows = `repeat(${Math.ceil(rows.length / 2)}, auto)`;
  el.recFields.style.gridAutoFlow = "column";
  el.recFields.replaceChildren(...rows);
}

/* 文字色設定 `通常色/変更色/引数/コード/型/プロパティ`。VBA は JScript で評価していた。
   VBA と同じく、値が日付のときだけ効く */
function applyColorRule(inp, f) {
  if (!f.color_rule || !f.color_rule.includes("/") || !f.value || !f.is_date) return;
  const parts = f.color_rule.split("/");
  if (parts.length < 6) return;
  try {
    const fn = new Function(parts[2], `return ${parts[3]}`);
    const arg = ["Date", "String", "Long"].includes(parts[4]) ? new Date(f.value.replace(/-/g, "/")) : f.value;
    const changed = !!fn(arg);
    const color = cssColor(!changed ? (parts[0] || "vbBlack") : parts[1]);
    if (/BackColor/i.test(parts[5])) inp.style.background = color; else inp.style.color = color;
  } catch (e) { /* 設定の不備。VBA も「十分注意」と書いている場所 */ }
}

/* ---------------- 補助入力(helpers.js) ---------------- */
let helpers = null;

async function helper(f, inp) {
  const result = await helpers.open(f, inp);
  if (result === undefined) return;
  inp.value = result.value;
  if (result.link !== undefined) { const j = document.getElementById(`join-${f.no}`); if (j) j.textContent = result.link; }
  markChanged(inp, f);
  if (f.kind === "calender" && f.link_field && f.link_func === "GetNewCode" && isDateVba(result.value)) {
    try {
      const res = await api.post("/api/record/new-code", { field: f.link_args[0], date: result.value, current: linkedInput(f.link_field)?.value || "" });
      const target = linkedInput(f.link_field);
      if (target) { target.value = res.code; markChanged(target, state.fields.find((x) => x.name === f.link_field)); }
    } catch (err) { toast(err.message, "warn"); }
  }
}

function linkedInput(name) { return el.recFields.querySelector(`[data-name="${CSS.escape(name)}"]`); }

/* ---------------- 実行(VBA REG_EXE → adoSQL) ---------------- */
function collect() {
  const values = {}; const originals = {};
  for (const f of state.fields) {
    const inp = el.recFields.querySelector(`[data-no="${f.no}"]`);
    values[f.name] = inp ? inp.value : f.value;
    originals[f.name] = f.original;
  }
  return { values, originals };
}

async function execute() {
  const { values, originals } = collect();
  const base = { mode: state.mode, key: state.key, values, originals };
  try {
    let res = await api.post("/api/record/execute", { ...base, confirmed: false });
    if (res.reason === "question") {
      const yes = confirm(res.message);
      res = await api.post("/api/record/execute", { ...base, confirmed: true, answer: yes });
    }
    if (res.ok) {
      toast(res.message, "ok");
      if (res.reset_filters) sessionStorage.removeItem("dbviewer.list");
      location.href = "/list";
      return;
    }
    if (res.message) toast(res.message, "warn");
  } catch (err) {
    if (err.body && err.body.reason === "required") {
      for (const no of err.body.fields_ng || []) { const inp = el.recFields.querySelector(`[data-no="${no}"]`); if (inp) inp.classList.add("is-required-ng"); }
      // 数値変換で変わった値を画面に戻す
      for (const f of state.fields) { const inp = el.recFields.querySelector(`[data-no="${f.no}"]`); if (inp && err.body.values && err.body.values[f.name] !== undefined) inp.value = err.body.values[f.name]; }
    }
    toast(err.message, err.status === 409 ? "error" : "warn");
  }
}

export function start(mode, key, click) {
  for (const id of ["recTitle", "recFields", "recProblem", "recNav", "navPrev", "navNext", "navKeys", "btnBack", "btnExe", "recMode",
                    "helper", "helperTitle", "helperNote", "helperBody", "helperOk", "helperClear"]) el[id] = document.getElementById(id);
  state.click = click || "ButtonClick";
  try { state.listSql = JSON.parse(sessionStorage.getItem("dbviewer.list") || "{}").sql || ""; } catch (e) { state.listSql = ""; }
  el.btnBack.addEventListener("click", () => { location.href = "/list"; });
  el.btnExe.addEventListener("click", execute);
  helpers = createHelpers({ listSql: () => state.listSql, parent: "MF1" });
  el.navKeys.addEventListener("change", () => loadForm(state.mode, el.navKeys.value));
  el.navPrev.addEventListener("click", () => { const i = state.keys.indexOf(state.key); if (i > 0) loadForm(state.mode, state.keys[i - 1]); });
  el.navNext.addEventListener("click", () => { const i = state.keys.indexOf(state.key); if (i >= 0 && i < state.keys.length - 1) loadForm(state.mode, state.keys[i + 1]); });
  loadForm(mode, key);
}
