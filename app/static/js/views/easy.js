/*
  かんたん設定(設定 → かんたん設定)。決めるのは 設定ファイル(権限・かんたん設定・接続先。0.22.0)と、
  列ごとの 表示・入力・印刷・クリック、アクセス権限の行。
  判断(誰が書けるか・値の確かめ)はサーバ(easy_setup.py / simple_table.py)。ここは選ばせて送るだけ。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";

const el = {};
let st = null;                    // /api/easy/state
let rights = null;                // /api/easy/rights(0.16.0: 編集可 / 閲覧のみ)
let access = null;                // /api/easy/access/rows(0.17.0: アクセス権限を直す)
let editing = null;               // 直している行(新しい行は {})

function show(id, on) { el[id].hidden = !on; }

async function load(table = "") {
  try {
    st = await api.post("/api/easy/state", table ? { table } : (st ? { table: st.table } : {}));
  } catch (err) {
    show("easyBody", false);
    show("easyProblem", true); el.easyProblem.textContent = err.message;
    return;
  }
  show("easyProblem", false); show("easyBody", true);
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

/* ---------------- 権限(設定ファイルの「アクセス権限」)・設定ファイル ---------------- */
async function loadRights() {
  try { rights = await api.post("/api/easy/rights", {}); } catch (err) { toastError(err); return; }
  const me = rights.me || {};
  el.rightsWho.textContent = `${me.login_id || "?"} / ${me.pc || "?"}`;
  el.rightsNow.textContent = me.label || "-";
  el.rightsNow.style.color = me.edit ? "var(--ok)" : "";
  el.rightsWhy.textContent = me.reason || "";
  const o = rights.master || {};
  el.rightsUsing.textContent = `${o.path || "?"}` + (o.found ? ` の「${o.table}」・ ${o.rows} 行` : ` ・ 読めません: ${o.problem}`);
  const rows = o.edit_rows || [];
  el.rightsHint.textContent = !o.found ? ""
    : !o.rows ? "アクセス権限の表に行がまだありません(誰でも 編集可)。下の「アクセス権限を直す」で行を足すと効き始めます。"
    : rows.length ? `権限が ${o.permission} の行(この行に当てはまる ID・PC が 編集可):`
    : `権限が ${o.permission} の行がありません(誰も直せません。管理者パスワードで行を足してください)。`;
  // 設定ファイル(0.22.0)
  const f = rights.file || {};
  el.sfNow.textContent = `いまの設定ファイル: ${f.path || "?"}${f.configured ? "" : "(この PC の既定の場所)"}`;
  const c = f.counts || {};
  el.sfCounts.textContent = f.problem ? `開けません: ${f.problem}`
    : `接続先 ${c["接続先"] ?? 0} 件 ・ かんたん設定 ${c["かんたん設定"] ?? 0} 表 ・ アクセス権限 ${c["アクセス権限"] ?? 0} 行`;
  if (document.activeElement !== el.sfText) el.sfText.value = f.configured || "";
  el.sfSave.disabled = el.sfDefault.disabled = !rights.can_change;
  el.sfSave.title = rights.can_change ? "" : "編集可 の権限か、管理者パスワードの鍵が要ります";
  el.rightsRowsWrap.hidden = !rows.length;
  el.rightsRows.replaceChildren(...rows.map((r) => {
    const tr = document.createElement("tr");
    for (const k of ["管理番号", "ログインID", "PC名", "有効", "備考"]) {
      const td = document.createElement("td"); td.textContent = r[k] ?? ""; tr.appendChild(td);
    }
    return tr;
  }));
}

async function saveSettingsFile(path) {
  try {
    const res = await api.post("/api/easy/settings-file", { path });
    // 権限・かんたん設定・接続先 が替わるので、画面ごと読み直す
    try { sessionStorage.setItem("dbviewer.dbswitch.note", res.message || ""); } catch (_) { /* 出せないだけ */ }
    location.reload();
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

/* ---------------- アクセス権限を直す(管理者パスワードで鍵を開けたとき。0.17.0) ---------------- */
const COLS = ["管理番号", "ログインID", "PC名", "権限", "有効", "備考"];
const isOn = (v) => !["0", "false", "×", "無効", "no", "off"].includes(String(v ?? "").trim().toLowerCase());

async function loadAccess() {
  try { access = await api.post("/api/easy/access/rows", {}); } catch (err) {
    access = null;
    el.accRows.replaceChildren();
    el.accNote2.textContent = err.message;
    showLock(false);
    return;
  }
  el.accNote2.textContent = `${access.path} の「アクセス権限」(${access.rows.length} 行)`;
  const keep = el.accPerm.value;
  el.accPerm.replaceChildren(...access.permissions.map((p) => option(p, p, p === keep)));
  showLock(access.admin);
  renderAccess();
}

function showLock(open) {
  el.accLocked.hidden = open;
  el.accOpen.hidden = !open;
  if (!open) { el.accForm.hidden = true; editing = null; }
}

function renderAccess() {
  if (!access) return;
  const me = access.me || {};
  const rows = access.rows;
  el.accRows.replaceChildren(...rows.map((r) => {
    const tr = document.createElement("tr");
    const mine = (!r["ログインID"] || String(r["ログインID"]).toLowerCase() === String(me.login_id).toLowerCase())
      && (!r["PC名"] || String(r["PC名"]).toLowerCase() === String(me.pc).toLowerCase())
      && (r["ログインID"] || r["PC名"]);
    tr.classList.toggle("is-mine", Boolean(mine));
    tr.classList.toggle("is-off", !isOn(r["有効"]));
    if (mine) tr.title = "この PC に当てはまる行";
    for (const k of COLS) {
      const td = document.createElement("td");
      td.textContent = k === "有効" ? (isOn(r[k]) ? "1" : "0") : (r[k] ?? "");
      tr.appendChild(td);
    }
    const ops = document.createElement("td"); ops.className = "ops";
    if (access.admin) {
      const ed = document.createElement("button"); ed.type = "button"; ed.className = "btn btn--find"; ed.textContent = "直す";
      ed.addEventListener("click", () => editRow(r));
      const del = document.createElement("button"); del.type = "button"; del.className = "btn btn--danger"; del.textContent = "消す";
      del.addEventListener("click", () => deleteRow(r));
      ops.append(ed, " ", del);
    }
    tr.appendChild(ops);
    return tr;
  }));
}

function editRow(r) {
  editing = r ? { ...r } : {};
  el.accNo.textContent = r ? String(r["管理番号"]) : "(新しい行)";
  el.accLogin.value = r ? (r["ログインID"] ?? "") : "";
  el.accPc.value = r ? (r["PC名"] ?? "") : "";
  const perm = r ? String(r["権限"] ?? "") : "編集可";
  if (perm && ![...el.accPerm.options].some((o) => o.value === perm)) el.accPerm.appendChild(option(perm, perm, false));
  el.accPerm.value = perm;
  el.accOn.checked = r ? isOn(r["有効"]) : true;
  el.accNote.value = r ? (r["備考"] ?? "") : "";
  el.accForm.hidden = false;
  el.accLogin.focus();
}

async function saveRow(event) {
  event.preventDefault();
  if (!editing) return;
  const row = { "管理番号": editing["管理番号"] ?? "", "ログインID": el.accLogin.value.trim(), "PC名": el.accPc.value.trim(),
                "権限": el.accPerm.value.trim(), "有効": el.accOn.checked ? 1 : 0, "備考": el.accNote.value.trim() };
  try {
    const res = await api.post("/api/easy/access/save", { row, seen: editing["管理番号"] ? editing : null });
    toast(res.message + (res.backup ? "(先に控えを取りました)" : ""), "ok");
    afterWrite(res);
  } catch (err) {
    toastError(err, "warn");
    if (err.status === 409 || err.status === 403) await loadAccess();
  }
}

async function deleteRow(r) {
  if (!(await confirmBox(`${r["管理番号"]} 番の行(${r["ログインID"] || "誰でも"} / ${r["PC名"] || "どの PC でも"} / ${r["権限"]})を消しますか？`,
                         { ok: "消す", danger: true }))) return;
  try {
    const res = await api.post("/api/easy/access/delete", { no: r["管理番号"], seen: r });
    toast(res.message, "warn");
    afterWrite(res);
  } catch (err) {
    toastError(err, "warn");
    if (err.status === 409 || err.status === 403) await loadAccess();
  }
}

async function afterWrite(res) {
  el.accForm.hidden = true;
  editing = null;
  const lv = document.getElementById("rb-level");             // 自分の権限が変わったかもしれない
  if (lv && res.label) lv.textContent = res.label;
  await loadAccess();
  await loadRights();
  await load(st ? st.table : "");
}

async function unlock() {
  try {
    const res = await api.post("/api/easy/access/unlock", { password: el.accPass.value });
    el.accPass.value = "";
    toast(res.message, "ok");
    await loadAccess();
    await loadRights();
  } catch (err) { toastError(err, "warn"); el.accPass.select(); }
}

async function lockAgain() {
  try { await api.post("/api/easy/access/unlock", { lock: true }); } catch (err) { toastError(err); }
  toast("鍵を掛けました。", "info");
  await loadAccess();
  await loadRights();
}

async function changePassword() {
  try {
    const res = await api.post("/api/easy/access/password", {
      current: el.accPwCur.value, new: el.accPwNew.value, confirm: el.accPwNew2.value });
    toast(res.message, "ok");
    el.accPwCur.value = el.accPwNew.value = el.accPwNew2.value = "";
  } catch (err) { toastError(err, "warn"); }
}

export function start() {
  for (const id of ["easyProblem", "easyBody", "easyDb", "easyGoSource", "easyTable", "easyColsWhere",
                    "easyLegacy", "easyCols", "easyColsSave", "easyColsHint", "easyVbaCard", "easyVbaList",
                    "rightsWho", "rightsNow", "rightsWhy", "rightsRecheck", "rightsUsing",
                    "sfNow", "sfCounts", "sfText", "sfSave", "sfDefault",
                    "rightsHint", "rightsRowsWrap", "rightsRows",
                    "accLocked", "accPass", "accUnlock", "accOpen", "accNew", "accLock", "accForm", "accNo",
                    "accLogin", "accMyId", "accPc", "accMyPc", "accPerm", "accOn", "accNote", "accCancel",
                    "accRows", "accNote2", "accPwCur", "accPwNew", "accPwNew2", "accPwChange",
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
  el.sfSave.addEventListener("click", () => saveSettingsFile(el.sfText.value.trim()));
  el.sfDefault.addEventListener("click", () => saveSettingsFile(""));
  el.rightsRecheck.addEventListener("click", recheckRights);
  el.accUnlock.addEventListener("click", unlock);
  el.accPass.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); unlock(); } });
  el.accLock.addEventListener("click", lockAgain);
  el.accNew.addEventListener("click", () => editRow(null));
  el.accCancel.addEventListener("click", () => { el.accForm.hidden = true; editing = null; });
  el.accForm.addEventListener("submit", saveRow);
  el.accMyId.addEventListener("click", () => { el.accLogin.value = (access && access.me.login_id) || ""; });
  el.accMyPc.addEventListener("click", () => { el.accPc.value = (access && access.me.pc) || ""; });
  el.accPwChange.addEventListener("click", changePassword);
  el.easyRetire.addEventListener("click", retireVba);
  el.easyGoSource.addEventListener("click", () => document.getElementById("tab-source")?.click());
  // タブを開くたびに読み直す(接続先で DB を替えたかもしれない)
  document.getElementById("tab-easy")?.addEventListener("click", () => { load(); loadRights(); loadAccess(); });
  load();
  loadRights();
  loadAccess();
}
