/*
  かんたん設定(設定 → かんたん設定)。決めるのは 見る DB と、列ごとの 表示・入力・印刷・クリック、
  権限を読む アクセス権限マスタ(0.16.0)。
  判断(誰が書けるか・値の確かめ)はサーバ(easy_setup.py / simple_table.py)。ここは選ばせて送るだけ。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";

const el = {};
let st = null;                    // /api/easy/state
let rights = null;                // /api/easy/rights(0.16.0: 編集可 / 閲覧のみ)

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
  show("easyLegacy", st.from_vba);
  el.easyLegacy.textContent = st.from_vba
    ? `この表の入力の種類は、VBA 版の [${st.table}_設定] から読んでいます。保存すると かんたん設定 に移ります。`
    : "";
  show("easyVbaCard", st.vba_tables.length > 0);
  el.easyVbaList.textContent = st.vba_tables.join("・");
  el.easyRetire.disabled = !st.can_write;
  el.easyCols.replaceChildren(...st.columns.map(columnRow));
  el.easyColsSave.disabled = !st.can_write;
  el.easyColsHint.textContent = st.can_write ? "" : "閲覧のみです(保存は 編集可 の権限で)。";
}

/* ---------------- 権限(梱包資材マスタの「アクセス権限」) ---------------- */
async function loadRights() {
  try { rights = await api.post("/api/easy/rights", {}); } catch (err) { toastError(err); return; }
  const me = rights.me || {};
  el.rightsWho.textContent = `${me.login_id || "?"} / ${me.pc || "?"}`;
  el.rightsNow.textContent = me.label || "-";
  el.rightsNow.style.color = me.edit ? "var(--ok)" : "";
  el.rightsWhy.textContent = me.reason || "";
  const o = rights.master || {};
  if (document.activeElement !== el.rightsPath) el.rightsPath.value = o.configured || "";
  el.rightsUsing.textContent = !o.path ? "(決めていません。誰でも 編集可)"
    : `${o.path}${o.auto ? "(見ている DB と同じフォルダ)" : ""}`
      + (o.found ? ` ・ ${o.rows} 行` : ` ・ 読めません: ${o.problem}`);
  el.rightsSave.disabled = !rights.can_change;
  el.rightsPath.disabled = !rights.can_change;
  const rows = o.edit_rows || [];
  el.rightsHint.textContent = !o.found ? ""
    : rows.length ? `${o.permission} の行(この行に当てはまる ID・PC が 編集可):`
    : `${o.permission} の行がまだありません。編集させたい人・PC の行を「${o.table}」に足してください`
      + "(権限 = dbviewer:edit、有効 = 1。ログインID・PC名の空欄は「問わない」)。";
  el.rightsRowsWrap.hidden = !rows.length;
  el.rightsRows.replaceChildren(...rows.map((r) => {
    const tr = document.createElement("tr");
    for (const k of ["管理番号", "ログインID", "PC名", "有効", "備考"]) {
      const td = document.createElement("td"); td.textContent = r[k] ?? ""; tr.appendChild(td);
    }
    return tr;
  }));
}

async function saveRightsPath() {
  try {
    const res = await api.post("/api/easy/rights", { path: el.rightsPath.value.trim(), save: true });
    toast(res.message || "保存しました。", "ok");
    rights = null;
    await recheckRights();
  } catch (err) { toastError(err, "warn"); }
}

async function recheckRights() {
  try {
    const s = await api.post("/api/login", {});
    const lv = document.getElementById("rb-level");          // 上の帯の権限も替える
    if (lv) lv.textContent = s.label;
    await loadRights();
    await load(st ? st.table : "");
  } catch (err) { toastError(err); }
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
  // クリック(0.12.0・0.13.0): 値を押したときにすること。開くときは「場所」({値} にマスの値が入る。空なら値そのもの)
  const clickTd = document.createElement("td");
  const clickPick = document.createElement("div"); clickPick.className = "pick";
  const click = document.createElement("select"); click.className = "input"; click.dataset.f = "click";
  click.setAttribute("aria-label", `${c.name} を押したとき`);
  for (const [v, label] of st.clicks || []) click.appendChild(option(v, label, v === (c.click || "")));
  const place = document.createElement("input"); place.className = "input"; place.dataset.f = "place";
  place.type = "text"; place.value = c.place || ""; place.style.width = "22em";
  place.placeholder = "場所: 空 = 値そのもの / 例 図面\\{値}.pdf ・ https://社内/?no={値}";
  place.title = "開くもの(Excel・Word・PDF・URL)は、値か この場所 から決まります。{値} にマスの値が入ります";
  place.setAttribute("aria-label", `${c.name} の開く場所`);
  const opens = () => ["open", "preview"].includes(click.value);
  place.hidden = !opens();
  click.addEventListener("change", () => { place.hidden = !opens(); });
  click.disabled = place.disabled = !st.can_write;
  clickPick.append(click, place);
  clickTd.appendChild(clickPick);
  tr.append(name, box("show", c.show, "表示"), kind, box("print", c.print, "印刷"), clickTd);
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
             step: f("step").value, print: f("print").checked,
             click: f("click").value,
             place: ["open", "preview"].includes(f("click").value) ? f("place").value.trim() : "" };
  });
}

async function saveColumns() {
  try {
    const res = await api.post("/api/easy/columns", { table: st.table, rows: collect() });
    toast(res.message, "ok");
    await load(st.table);
  } catch (err) { toastError(err, "warn"); }
}

async function retireVba() {
  if (!(await confirmBox(`VBA 版の設定の表(${st.vba_tables.join("・")})を消します。入力の種類は かんたん設定 に取り込みます。よろしいですか？`,
                         { ok: "取り込んで消す", danger: true }))) return;
  try {
    const res = await api.post("/api/easy/retire", {});
    toast(res.message, "ok");
    await load(st.table);
  } catch (err) { toastError(err, "warn"); }
}

export function start() {
  for (const id of ["easyLogin", "easyProblem", "easyBody", "easyDb", "easyGoSource", "easyTable", "easyColsWhere",
                    "easyLegacy", "easyCols", "easyColsSave", "easyColsHint", "easyVbaCard", "easyVbaList",
                    "rightsWho", "rightsNow", "rightsWhy", "rightsPath", "rightsSave", "rightsRecheck", "rightsUsing",
                    "rightsHint", "rightsRowsWrap", "rightsRows",
                    "easyRetire"]) {
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
  el.rightsSave.addEventListener("click", saveRightsPath);
  el.rightsRecheck.addEventListener("click", recheckRights);
  el.easyRetire.addEventListener("click", retireVba);
  el.easyGoSource.addEventListener("click", () => document.getElementById("tab-source")?.click());
  // タブを開くたびに読み直す(接続先で DB を替えたかもしれない)
  document.getElementById("tab-easy")?.addEventListener("click", () => { load(); loadRights(); });
  load();
  loadRights();
}
