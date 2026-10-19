/*
  dbswitch.js — 上の帯の「DB」から、見る DB を選び直す(0.20.0)

  .sqlite3 が何個もあると、毎回フルパスを打つのは面倒。そこで:
  - この PC で**最近開いた DB**(新しい順・10 個まで)と、**いまの DB と同じフォルダの .sqlite3** を並べる
  - 押せばすぐその DB に切り替える(中身が開ける .sqlite3 かはサーバが確かめる。だめなら理由を出して今のまま)
  - 見つからなくなったもの・もう使わないものは × で「最近」から外せる(ファイルは消さない)
  設定 → 接続先 の「最近開いた DB」も同じものを出す(`renderRecent`)。
*/
import { api } from "./api.js";
import { toast, toastError } from "./toast.js";

const NOTE_KEY = "dbviewer.dbswitch.note";

export async function openDb(path) {
  const res = await api.post("/api/settings/open-db", { path });
  // 切り替えたら画面を読み直す(いまの表・設定は前の DB のもの)。知らせは読み直したあとに出す
  try { sessionStorage.setItem(NOTE_KEY, res.message || ""); } catch (_) { /* 出せないだけ */ }
  location.reload();
  return res;
}

/** 読み直したあとに、切り替えた知らせを出す(app.js が起動時に呼ぶ)。 */
export function showPendingNote() {
  let text = "";
  try { text = sessionStorage.getItem(NOTE_KEY) || ""; sessionStorage.removeItem(NOTE_KEY); } catch (_) { return; }
  if (text) toast(text, "ok");
}

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

function row(item, { removable = true } = {}) {
  const wrap = node("div", "dbmenu__row" + (item.current ? " is-current" : "") + (item.exists ? "" : " is-missing"));
  const pick = node("button");
  pick.type = "button";
  pick.setAttribute("role", "menuitem");
  pick.dataset.open = item.path;
  if (item.current) pick.dataset.current = "1";
  pick.title = item.path;
  pick.append(node("b", "", item.name + (item.current ? "(いま開いている)" : "")),
               node("small", "", item.exists ? `${item.folder}${item.mtime ? " ・ " + item.mtime : ""}`
                                             : `見つかりません: ${item.folder}`));
  wrap.appendChild(pick);
  if (removable && !item.current) {
    const x = node("button", "dbmenu__x", "×");
    x.type = "button";
    x.dataset.forget = item.path;
    x.title = "「最近開いた DB」から外す(ファイルは消しません)";
    x.setAttribute("aria-label", `${item.name} を最近開いた DB から外す`);
    wrap.appendChild(x);
  }
  return wrap;
}

function render(menu, data) {
  const parts = [];
  if (data.recent.length) {
    parts.push(node("div", "dbmenu__h", "最近開いた DB"), ...data.recent.map((r) => row(r)));
  }
  if (data.same_folder.length) {
    parts.push(node("div", "dbmenu__h", "同じフォルダの .sqlite3"),
               ...data.same_folder.map((r) => row(r, { removable: false })));
  }
  if (data.message) parts.push(node("div", "dbmenu__note", data.message));
  if (!parts.length) parts.push(node("div", "dbmenu__note", "まだ DB を開いていません。下の「ほかの DB を探す」から選んでください。"));
  const foot = node("div", "dbmenu__foot");
  const more = node("button");
  more.type = "button";
  more.dataset.more = "1";
  more.append(node("b", "", "ほかの DB を探す…"), node("small", "", "設定 → 接続先(パスを入れる・参照… で選ぶ)"));
  foot.appendChild(more);
  parts.push(foot);
  menu.replaceChildren(...parts);
}

let current = null;           // いま帯にあるメニュー(帯は画面を移るたびに作り直される)
let documentWired = false;

function close() {
  if (!current) return;
  current.menu.hidden = true;
  current.button.setAttribute("aria-expanded", "false");
}

/** 帯の DB ボタンを繋ぐ。`wireShell` から画面ごとに呼ばれる。 */
export function wire() {
  const button = document.getElementById("rb-db");
  const menu = document.getElementById("dbMenu");
  if (!button || !menu || button.dataset.wired === "1") return;
  button.dataset.wired = "1";
  current = { button, menu };
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (!menu.hidden) { close(); return; }
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    menu.replaceChildren(node("div", "dbmenu__note", "読み込み中…"));
    try {
      render(menu, await api.get("/api/settings/db-choices"));
      menu.querySelector("button:not([data-current])")?.focus();
    } catch (err) {
      menu.replaceChildren(node("div", "dbmenu__note", err.message));
    }
  });
  menu.addEventListener("click", async (event) => {
    event.stopPropagation();
    const forget = event.target.closest("[data-forget]");
    if (forget) {
      try { render(menu, await api.post("/api/settings/db-forget", { path: forget.dataset.forget })); }
      catch (err) { toastError(err); }
      return;
    }
    if (event.target.closest("[data-more]")) { location.href = "/settings?tab=source"; return; }
    const pick = event.target.closest("[data-open]");
    if (!pick) return;
    if (pick.dataset.current) { close(); return; }
    try { await openDb(pick.dataset.open); } catch (err) { toastError(err); }
  });
  if (!documentWired) {
    documentWired = true;
    document.addEventListener("click", (event) => {
      if (current && !current.menu.contains(event.target)) close();
    });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  }
}

/** 設定 → 接続先 の「最近開いた DB」(開く・外す)。 */
export async function renderRecent(list, { onChange } = {}) {
  if (!list) return;
  const draw = (data) => {
    list.replaceChildren(...(data.recent.length ? data.recent.map((r) => row(r))
      : [node("div", "dbmenu__note", "まだありません。DB を開くとここに並びます。")]));
  };
  if (list.dataset.wired !== "1") {
    list.dataset.wired = "1";
    list.addEventListener("click", async (event) => {
      const forget = event.target.closest("[data-forget]");
      if (forget) {
        try { draw(await api.post("/api/settings/db-forget", { path: forget.dataset.forget })); }
        catch (err) { toastError(err); }
        return;
      }
      const pick = event.target.closest("[data-open]");
      if (!pick || pick.dataset.current) return;
      try { await openDb(pick.dataset.open); if (onChange) onChange(); } catch (err) { toastError(err); }
    });
  }
  try { draw(await api.get("/api/settings/db-choices")); } catch (err) { toastError(err); }
}
