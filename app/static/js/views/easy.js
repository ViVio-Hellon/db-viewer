/*
  かんたん設定(設定 → かんたん設定)。決めるのは 見る DB と、列ごとの 表示・入力・印刷 だけ。
  判断(誰が書けるか・値の確かめ)はサーバ(easy_setup.py / simple_table.py)。ここは選ばせて送るだけ。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";

const el = {};
let st = null;                    // /api/easy/state
let sessionId = "";               // ログインした ID(ログインを使うで登録する ID)

function show(id, on) { el[id].hidden = !on; }

async function load(table = "") {
  try {
    st = await api.post("/api/easy/state", table ? { table } : (st ? { table: st.table } : {}));
  } catch (err) {
    show("easyBody", false);
    if (err.status === 401) { show("easyLogin", true); return; }
    show("easyProblem", true); el.easyProblem.textContent = err.message;
    return;
  }
  show("easyLogin", false); show("easyProblem", false); show("easyBody", true);
  render();
}

function option(value, label, selected) {
  const o = document.createElement("option");
  o.value = value; o.textContent = label; o.selected = selected;
  return o;
}

function render() {
  el.easyDb.textContent = st.db || "(接続先が決まっていません)";
  el.easyTable.replaceChildren(...st.tables.map((t) =>
    option(t.name, `${t.name}(${t.rows >= 0 ? t.rows.toLocaleString() : "?"} 件)`, t.name === st.table)));
  el.easyColsWhere.textContent = `[${st.table}] の ${st.columns.length} 列`;
  show("easyLegacy", st.legacy);
  el.easyLegacy.textContent = st.legacy
    ? `この表には VBA 版の設定 [${st.setting_table}] があるので、一覧の入力は VBA 版の決まりで動きます(ここの「入力」はマスタ管理で効きます)。表示・印刷はここで決めたとおり。`
    : "";
  el.easyCols.replaceChildren(...st.columns.map(columnRow));
  el.easyColsSave.disabled = !st.can_write;
  el.easyColsHint.textContent = st.can_write ? "" : "見るだけです(保存は Level 5)。";

  // ログイン
  show("easyStartBox", !st.has_id);
  el.easyStartId.textContent = sessionId || "(ログインした ID)";
  el.easyIdState.textContent = st.has_id
    ? "ID の表で、人とレベルを決めています(人を足すのは マスタ管理 の ID)。"
    : "この DB には ID の表が無いので、誰でも見て直せます。人とレベルを決めたいときだけ、自分を登録してログインを使います。";
}

function columnRow(c) {
  const tr = document.createElement("tr");
  tr.dataset.name = c.name;
  tr.classList.toggle("is-off", !c.show);
  const name = document.createElement("td");
  name.className = "name";
  const b = document.createElement("b"); b.textContent = c.name;
  const small = document.createElement("small"); small.textContent = c.type || "";
  name.append(b, small);

  const box = (f, on, label) => {
    const td = document.createElement("td");
    const x = document.createElement("input"); x.type = "checkbox"; x.checked = on; x.dataset.f = f;
    x.setAttribute("aria-label", `${c.name} を${label}`);
    x.disabled = !st.can_write;
    td.appendChild(x);
    return td;
  };
  const kind = document.createElement("td");
  const pick = document.createElement("div"); pick.className = "pick";
  const sel = document.createElement("select"); sel.className = "input"; sel.dataset.f = "input";
  sel.setAttribute("aria-label", `${c.name} の入力`);
  for (const [v, label] of st.inputs) sel.appendChild(option(v, label, v === c.input));
  const stepLab = document.createElement("label"); stepLab.className = "checkline";
  const step = document.createElement("input"); step.className = "input"; step.dataset.f = "step";
  step.type = "number"; step.min = "0"; step.step = "any"; step.value = c.step || "1"; step.style.width = "6em";
  step.setAttribute("aria-label", `${c.name} の刻み`);
  stepLab.append(document.createTextNode("刻み "), step);
  stepLab.hidden = c.input !== "spin";
  sel.addEventListener("change", () => { stepLab.hidden = sel.value !== "spin"; });
  sel.disabled = step.disabled = !st.can_write;
  pick.append(sel, stepLab);
  kind.appendChild(pick);
  tr.append(name, box("show", c.show, "表示"), kind, box("print", c.print, "印刷"));
  tr.addEventListener("change", () => {
    tr.classList.add("is-changed");
    tr.classList.toggle("is-off", !tr.querySelector('[data-f="show"]').checked);
  });
  return tr;
}

function collect() {
  return [...el.easyCols.children].map((tr) => {
    const f = (name) => tr.querySelector(`[data-f="${name}"]`);
    return { name: tr.dataset.name, show: f("show").checked, input: f("input").value,
             step: f("step").value, print: f("print").checked };
  });
}

async function saveColumns() {
  try {
    const res = await api.post("/api/easy/columns", { table: st.table, rows: collect() });
    toast(res.message, "ok");
    await load(st.table);
  } catch (err) { toastError(err, "warn"); }
}

async function startLogin() {
  const password = el.easyStartPass.value;
  if (!password) { toast("パスワードを入れてください。", "warn"); el.easyStartPass.focus(); return; }
  if (!(await confirmBox("あなたを Level 5(管理者)で登録して、この DB に ID の表を作ります。次からはログインが要ります。よろしいですか？",
                         { ok: "登録する" }))) return;
  try {
    const res = await api.post("/api/easy/start", { password, name: el.easyStartName.value });
    el.easyStartPass.value = "";
    toast(res.message, "ok");
    const lv = document.getElementById("rb-level");          // 上の帯のレベルも替える
    if (lv && res.level) lv.textContent = String(res.level);
    await load(st.table);
  } catch (err) { toastError(err, "warn"); }
}

async function loadSession() {
  try {
    const s = await api.get("/api/session");
    sessionId = s.user_id || "";
  } catch { /* 出せないだけ */ }
}

export function start() {
  for (const id of ["easyLogin", "easyProblem", "easyBody", "easyDb", "easyGoSource", "easyTable", "easyColsWhere",
                    "easyLegacy", "easyCols", "easyColsSave", "easyColsHint", "easyIdState", "easyStartBox",
                    "easyStartId", "easyStartName", "easyStartPass", "easyStart"]) {
    el[id] = document.getElementById(id);
  }
  if (!el.easyBody) return;
  el.easyTable.addEventListener("change", async () => {
    if (el.easyCols.querySelector(".is-changed")
        && !(await confirmBox("保存していない変更があります。表を替えますか？", { ok: "替える" }))) {
      el.easyTable.value = st.table; return;
    }
    load(el.easyTable.value);
  });
  el.easyColsSave.addEventListener("click", saveColumns);
  el.easyStart.addEventListener("click", startLogin);
  el.easyGoSource.addEventListener("click", () => document.getElementById("tab-source")?.click());
  // タブを開くたびに読み直す(接続先で DB を替えたかもしれない)
  document.getElementById("tab-easy")?.addEventListener("click", () => load());
  loadSession().then(() => load());
}
