/*
  接続先の登録(設定 → 接続先。0.32.0)。設定ファイルの「接続先」表をそのまま見て、足す・直す・消す。
  直せるのは 編集可 の人か、管理者パスワードで鍵を開けたとき(サーバが決める)。消しても DB ファイルは消さない。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";
import * as dbswitch from "../dbswitch.js";

const el = {};
const NOTE_KEY = "dbviewer.dbswitch.note";
let state = null;

function node(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function input(value, placeholder, id) {
  const i = node("input", "input");
  i.value = value ?? "";
  i.spellcheck = false;
  i.autocomplete = "off";
  if (placeholder) i.placeholder = placeholder;
  if (id) i.id = id;
  i.disabled = !state.can_change;
  return i;
}

function stateCell(r) {
  const td = node("td", "t state");
  if (r.current) { td.classList.add("is-current"); td.textContent = "いま開いている"; }
  else if (!String(r["パス"] || "").trim()) { td.classList.add("is-missing"); td.textContent = "パスが空"; }
  else if (!r.exists) { td.classList.add("is-missing"); td.textContent = "見つかりません"; }
  else td.textContent = "あります";
  return td;
}

function render() {
  const can = state.can_change;
  el.dbregLock.hidden = can;
  el.dbregLockWhy.textContent = can ? "" : "直すのは 編集可 の人か、管理者パスワードで鍵を開けたときだけです。見るだけならそのままで。";
  el.dbListWhere.textContent = `${state.rows.length} 件 ・ ${state.file}`;
  el.dbregRows.replaceChildren(...state.rows.map((r) => {
    const tr = node("tr");
    const name = input(r["名前"], "名前");
    const path = input(r["パス"], "DB ファイルのフルパス");
    const note = input(r["備考"], "");
    const save = node("button", "btn btn--sm", "直す");
    save.type = "button"; save.hidden = true; save.disabled = !can;
    const del = node("button", "btn btn--sm btn--danger", "消す");
    del.type = "button"; del.disabled = !can;
    const dirty = () => {
      const changed = name.value !== (r["名前"] ?? "") || path.value !== (r["パス"] ?? "") || note.value !== (r["備考"] ?? "");
      tr.classList.toggle("is-dirty", changed);
      save.hidden = !changed;
    };
    for (const i of [name, path, note]) i.addEventListener("input", dirty);
    save.addEventListener("click", () => send("/api/settings/dbs/save", {
      row: { 管理番号: r["管理番号"], 名前: name.value, パス: path.value, 備考: note.value }, seen: seen(r) }));
    del.addEventListener("click", async () => {
      const ask = r.current
        ? `${r["管理番号"]} 番(いま開いている DB)を接続先から消します。見る DB の選択も外れます(ファイルは消しません)。よろしいですか？`
        : `${r["管理番号"]} 番「${r["名前"] || r["パス"]}」を接続先から消します(ファイルは消しません)。よろしいですか？`;
      if (!(await confirmBox(ask, { ok: "消す", danger: true }))) return;
      send("/api/settings/dbs/delete", { no: r["管理番号"], seen: seen(r) });
    });
    const no = node("td", "t", String(r["管理番号"]));
    const tdName = node("td", "t"); tdName.appendChild(name);
    const tdPath = node("td", "t path"); tdPath.appendChild(path);
    const tdNote = node("td", "t"); tdNote.appendChild(note);
    const act = node("td", "t act"); act.append(save, " ", del);
    tr.append(no, tdName, tdPath, tdNote, stateCell(r), act);
    return tr;
  }));
  if (!state.rows.length) {
    const tr = node("tr"); const td = node("td", "t", "接続先がまだありません。下の行で足してください。");
    td.colSpan = 6; tr.appendChild(td); el.dbregRows.appendChild(tr);
  }
  // 足す行
  const tr = node("tr");
  const name = input("", "名前(空ならファイル名)");
  const path = input("", "例: \\\\server\\共有\\作業標準.sqlite3", "dbregNewPath");
  const note = input("", "備考");
  const browse = node("button", "btn btn--find btn--sm", "▸ 参照…");
  browse.type = "button"; browse.disabled = !can;
  browse.dataset.browse = "dbregNewPath"; browse.dataset.browseFile = "1";
  const add = node("button", "btn btn--commit btn--sm", "足す");
  add.type = "button"; add.disabled = !can;
  add.addEventListener("click", () => send("/api/settings/dbs/save",
    { row: { 名前: name.value, パス: path.value, 備考: note.value } }));
  const tdName = node("td", "t"); tdName.appendChild(name);
  const tdPath = node("td", "t path"); tdPath.append(path);
  const tdNote = node("td", "t"); tdNote.appendChild(note);
  const act = node("td", "t act"); act.append(browse, " ", add);
  tr.append(node("td", "t", "新"), tdName, tdPath, tdNote, node("td", "t state", ""), act);
  el.dbregNew.replaceChildren(tr);
}

function seen(r) {
  return { 管理番号: r["管理番号"], 名前: r["名前"], パス: r["パス"], 備考: r["備考"] };
}

async function send(path, body) {
  el.dbregWhy.hidden = true;
  try {
    const out = await api.post(path, body);
    if (out.reload) {                                   // いま開いている DB を直した・消した → 読み直す
      try { sessionStorage.setItem(NOTE_KEY, out.message || ""); } catch (_) { /* 出せないだけ */ }
      location.reload();
      return;
    }
    state = out;
    render();
    refreshSelect();
    toast(out.message, "ok");
  } catch (err) {
    el.dbregWhy.hidden = false;
    el.dbregWhy.textContent = err.message;
  }
}

function refreshSelect() {
  dbswitch.renderList(null, { select: document.getElementById("dbSelect"), clear: document.getElementById("dbClear"),
    addBox: document.getElementById("dbAddBox") });
}

async function load() {
  try {
    state = await api.get("/api/settings/dbs");
    render();
  } catch (err) {
    el.dbregWhy.hidden = false;
    el.dbregWhy.textContent = err.message;
  }
}

export function start() {
  for (const id of ["dbregRows", "dbregNew", "dbregWhy", "dbregLock", "dbregLockWhy", "dbregPass", "dbregUnlock",
    "dbListWhere"]) {
    el[id] = document.getElementById(id);
  }
  if (!el.dbregRows) return;
  el.dbregUnlock.addEventListener("click", async () => {
    try {
      await api.post("/api/easy/access/unlock", { password: el.dbregPass.value });
      el.dbregPass.value = "";
      await load();
      toast("鍵を開けました(30 分さわらなければ掛かります)。", "ok");
    } catch (err) { toastError(err); }
  });
  el.dbregPass.addEventListener("keydown", (e) => { if (e.key === "Enter") el.dbregUnlock.click(); });
  load();
}
