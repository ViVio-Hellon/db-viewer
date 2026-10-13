/*
  かんたん設定(設定 → かんたん設定)。判断(誰が書けるか・書き方)はサーバ(easy_setup.py)。
  ここは選ばせて送るだけ。設定の表(全般設定・<表>_設定・ID)の書き方は見せない。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";

const el = {};
let st = null;                    // /api/easy/state
let sessionId = "";               // ログインした ID(編集を始めるで登録する ID)
const CUSTOM = "custom";

function show(id, on) { el[id].hidden = !on; }

async function load() {
  try {
    st = await api.post("/api/easy/state", {});
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
  // 表示する表
  el.easyMain.replaceChildren(...st.tables.map((t) =>
    option(t.name, `${t.name}(${t.rows >= 0 ? t.rows.toLocaleString() : "?"} 件)`, t.name === st.main)));
  el.easyMainNote.textContent = st.can_write
    ? (st.auto_main ? "いまは自動で選んでいます。選ぶと、この DB を使う全員の一覧がこの表になります。"
                    : "全般設定「メインテーブル名」に書きます。この DB を使う全員の一覧が変わります。")
    : (st.auto_main ? "この PC だけで切り替えます(DB には書きません)。全員の決まりにするのは Level 5 の人です。"
                    : "表示する表は全般設定で決まっています。変えるには Level 5 が要ります。");
  el.easyMainSave.disabled = !st.can_write && !st.auto_main;

  // 編集を始める
  show("easyStartCard", st.can_start);
  el.easyStartId.textContent = sessionId || "(ログインした ID)";

  // 列ごとの決まり
  el.easyColsWhere.textContent = `[${st.main}] の ${st.columns.length} 列`;
  el.easyConvName.textContent = `[${st.conv_table}]`;
  show("easyKeyNote", !st.key_ok);
  el.easyKey.disabled = !st.can_write;
  el.easyCols.replaceChildren(...st.columns.map(columnRow));
  el.easyColsSave.disabled = !st.can_write;
  el.easyColsHint.textContent = st.can_write
    ? (st.auto_conv ? `保存すると [${st.conv_table}] を作ります(いまは無いので、どの列も自由入力)。` : "")
    : "見るだけです(保存は Level 5)。";
}

function columnRow(c) {
  const tr = document.createElement("tr");
  tr.dataset.name = c.name;
  const name = document.createElement("td");
  name.className = "name";
  const b = document.createElement("b"); b.textContent = c.name;
  const small = document.createElement("small"); small.textContent = c.type || "";
  name.append(b, small);

  // 入力の種類(+ 選ぶ表 / そのままの書き方)
  const kind = document.createElement("td");
  const pick = document.createElement("div"); pick.className = "pick";
  const sel = document.createElement("select"); sel.className = "input"; sel.dataset.f = "input";
  sel.setAttribute("aria-label", `${c.name} の入力の種類`);
  for (const [v, label] of st.inputs) sel.appendChild(option(v, label, v === c.input));
  if (c.input === CUSTOM) sel.appendChild(option(CUSTOM, `そのまま: ${c.raw}`, true));
  const master = document.createElement("select"); master.className = "input"; master.dataset.f = "master";
  master.setAttribute("aria-label", `${c.name} で選ぶ表`);
  master.appendChild(option("", "(表を選ぶ)", !c.master));
  for (const m of st.masters) master.appendChild(option(m, m, m === c.master));
  const needsMaster = () => (st.inputs.find(([v]) => v === sel.value) || [])[2];
  master.hidden = !needsMaster();
  sel.addEventListener("change", () => { master.hidden = !needsMaster(); });
  pick.append(sel, master);
  kind.appendChild(pick);

  const check = (f, on, label) => {
    const td = document.createElement("td");
    const box = document.createElement("input"); box.type = "checkbox"; box.checked = on; box.dataset.f = f;
    box.setAttribute("aria-label", `${c.name} ${label}`);
    td.appendChild(box);
    return td;
  };
  const sort = document.createElement("td");
  const lab = document.createElement("label"); lab.className = "checkline";
  const radio = document.createElement("input"); radio.type = "radio"; radio.name = "easySort"; radio.dataset.f = "sorted";
  radio.checked = c.sorted;
  const dir = document.createElement("select"); dir.className = "input"; dir.dataset.f = "sort";
  dir.append(option("ASC", "小さい順", c.sort !== "DESC"), option("DESC", "大きい順", c.sort === "DESC"));
  dir.style.width = "8.5em";
  lab.append(radio);
  sort.append(lab, dir);
  for (const n of [sel, master, dir]) n.disabled = !st.can_write;
  tr.append(name, kind, check("required", c.required, "必須"), check("money", c.money, "金額で見せる"), sort);
  for (const n of tr.querySelectorAll("input")) n.disabled = !st.can_write;
  tr.addEventListener("change", () => tr.classList.add("is-changed"));
  return tr;
}

function collect() {
  return [...el.easyCols.children].map((tr) => {
    const f = (name) => tr.querySelector(`[data-f="${name}"]`);
    const c = st.columns.find((x) => x.name === tr.dataset.name) || {};
    return {
      name: tr.dataset.name, input: f("input").value, master: f("master").value, raw: c.raw || "",
      required: f("required").checked, money: f("money").checked,
      sorted: f("sorted").checked, sort: f("sort").value,
    };
  });
}

async function saveMain() {
  const table = el.easyMain.value;
  if (!table || table === st.main) { toast("いまの表と同じです。", "info"); return; }
  try {
    const res = await api.post("/api/easy/main", { table });
    toast(res.message, "ok");
    await load();
  } catch (err) { toastError(err, "warn"); }
}

async function saveColumns() {
  try {
    const res = await api.post("/api/easy/columns", { rows: collect() });
    toast(res.message, "ok");
    await load();
  } catch (err) { toastError(err, "warn"); }
}

async function startEditing() {
  const password = el.easyStartPass.value;
  if (!password) { toast("パスワードを入れてください。", "warn"); el.easyStartPass.focus(); return; }
  if (!(await confirmBox("あなたを Level 5(管理者)で登録して、この DB に ID の表を作ります。よろしいですか？",
                         { ok: "登録する" }))) return;
  try {
    const res = await api.post("/api/easy/start", { password, name: el.easyStartName.value });
    el.easyStartPass.value = "";
    toast(res.message, "ok");
    const lv = document.getElementById("rb-level");          // 上の帯のレベルも替える
    if (lv && res.level) lv.textContent = String(res.level);
    await load();
  } catch (err) { toastError(err, "warn"); }
}

async function addKey() {
  if (!(await confirmBox(`[${st.main}] のいちばん右に「${st.key_item}」の列を足し、いまの行に 1 から番号を振ります。よろしいですか？`,
                         { ok: "足す" }))) return;
  try {
    const res = await api.post("/api/easy/key", {});
    toast(res.message, "ok");
    await load();
  } catch (err) { toastError(err, "warn"); }
}

async function loadSession() {
  try {
    const s = await api.get("/api/session");
    sessionId = s.user_id || "";
  } catch { /* 出せないだけ */ }
}

export function start() {
  for (const id of ["easyLogin", "easyProblem", "easyBody", "easyMain", "easyMainSave", "easyMainNote",
                    "easyStartCard", "easyStartId", "easyStartName", "easyStartPass", "easyStart",
                    "easyColsWhere", "easyConvName", "easyKeyNote", "easyKey", "easyCols", "easyColsSave", "easyColsHint"]) {
    el[id] = document.getElementById(id);
  }
  if (!el.easyBody) return;
  el.easyMainSave.addEventListener("click", saveMain);
  el.easyColsSave.addEventListener("click", saveColumns);
  el.easyStart.addEventListener("click", startEditing);
  el.easyKey.addEventListener("click", addKey);
  // タブを開くたびに読み直す(ほかのタブ・画面で DB を替えたかもしれない)
  document.getElementById("tab-easy")?.addEventListener("click", load);
  loadSession().then(load);
}
