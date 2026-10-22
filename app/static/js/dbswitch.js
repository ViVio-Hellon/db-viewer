/*
  dbswitch.js — 見る DB を選び直す(0.20.0。0.22.0 から一覧は設定ファイルの「接続先」)

  - **接続先**(設定ファイルの一覧)を並べる。押せばすぐその DB に切り替える
    (開ける .sqlite3 かはサーバが確かめる。だめなら理由を出して今のまま)
  - 編集可 の人には、**いまの DB と同じフォルダの .sqlite3**(一覧に無いもの)も出す。押すと足して開く
  - × で接続先から外す(編集可 の人だけ。ファイルは消さない)。いま開いている DB も外せる(選択も外れる。0.31.0)
  - 「選択を外す」で、この PC で見る DB を決めていない状態に戻す(0.31.0。一覧はそのまま)
  上の帯の「DB」のメニューと、設定 → 接続先 のコンボボックス・一覧(`renderList`)で同じものを出す。
*/
import { api } from "./api.js";
import { toast, toastError } from "./toast.js";
import { confirmBox } from "./askbox.js";

const NOTE_KEY = "dbviewer.dbswitch.note";

export async function openDb(path) {
  const res = await api.post("/api/settings/open-db", { path });
  // 切り替えたら画面を読み直す(いまの表・設定は前の DB のもの)。知らせは読み直したあとに出す
  try { sessionStorage.setItem(NOTE_KEY, res.message || ""); } catch (_) { /* 出せないだけ */ }
  location.reload();
  return res;
}

function reloadWith(note) {
  try { sessionStorage.setItem(NOTE_KEY, note || ""); } catch (_) { /* 出せないだけ */ }
  location.reload();
}

/** 接続先から外す。いま開いている DB なら確かめてから外し、選択も外れるので画面を読み直す。
    外したあとの一覧を返す(読み直すときと、やめたときは null)。 */
async function forgetDb(button) {
  const current = button.dataset.forgetCurrent === "1";
  if (current && !(await confirmBox("いま開いている DB を接続先から外します。見る DB の選択も外れます"
      + "(ファイルは消しません)。よろしいですか？", { ok: "外す", danger: true }))) return null;
  const res = await api.post("/api/settings/db-forget", { path: button.dataset.forget });
  if (res.cleared) { reloadWith(res.message_done); return null; }
  return res;
}

/** 見る DB の選択を外す(この PC だけ。接続先の一覧はそのまま)。 */
export async function clearDb() {
  const res = await api.post("/api/settings/db-clear", {});
  reloadWith(res.message_done);
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

function row(item, { removable = false } = {}) {
  const wrap = node("div", "dbmenu__row" + (item.current ? " is-current" : "") + (item.exists ? "" : " is-missing"));
  const pick = node("button");
  pick.type = "button";
  pick.setAttribute("role", "menuitem");
  pick.dataset.open = item.path;
  if (item.current) pick.dataset.current = "1";
  pick.title = item.path;
  const label = item.name && item.name !== item.file ? `${item.name}(${item.file})` : item.file || item.name;
  pick.append(node("b", "", label + (item.current ? "(いま開いている)" : "")),
               node("small", "", item.exists ? `${item.folder}${item.mtime ? " ・ " + item.mtime : ""}`
                                             : `見つかりません: ${item.folder}`));
  wrap.appendChild(pick);
  if (removable) {
    const x = node("button", "dbmenu__x", "×");
    x.type = "button";
    x.dataset.forget = item.path;
    if (item.current) x.dataset.forgetCurrent = "1";
    x.title = item.current ? "接続先から外し、見る DB の選択も外す(ファイルは消しません)"
      : "接続先から外す(ファイルは消しません)";
    x.setAttribute("aria-label", `${item.name} を接続先から外す`);
    wrap.appendChild(x);
  }
  return wrap;
}

function render(menu, data) {
  const parts = [];
  if (data.dbs.length) {
    parts.push(node("div", "dbmenu__h", "接続先"), ...data.dbs.map((r) => row(r, { removable: data.can_add })));
  }
  if (data.same_folder.length) {
    parts.push(node("div", "dbmenu__h", "同じフォルダの .sqlite3(押すと接続先に足して開く)"),
               ...data.same_folder.map((r) => row(r)));
  }
  if (data.message) parts.push(node("div", "dbmenu__note", data.message));
  if (!parts.length) parts.push(node("div", "dbmenu__note", "接続先がまだありません。下の「ほかの DB を探す」から足してください。"));
  const foot = node("div", "dbmenu__foot");
  const more = node("button");
  more.type = "button";
  more.dataset.more = "1";
  more.append(node("b", "", data.can_add ? "ほかの DB を足す…" : "接続先の設定へ…"),
              node("small", "", data.can_add ? "設定 → 接続先(パスを入れる・参照… で選ぶ)" : "設定 → 接続先"));
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
      try { const res = await forgetDb(forget); if (res) render(menu, res); }
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

/** 設定 → 接続先: コンボボックス・一覧(開く・外す)・足す欄(足せる人だけ)。 */
export async function renderList(list, { select = null, addBox = null, where = null, clear = null } = {}) {
  const draw = (data) => {
    if (list) {
      list.replaceChildren(...(data.dbs.length ? data.dbs.map((r) => row(r, { removable: data.can_add }))
        : [node("div", "dbmenu__note", "接続先がまだありません。上の「接続先を足す」で足してください。")]));
    }
    if (where) where.textContent = `${data.dbs.length} 件 ・ ${data.settings_file}`;
    if (addBox) addBox.hidden = !data.can_add;
    if (select) {
      const opts = data.dbs.map((r) => {
        const o = node("option", "", `${r.name || r.file} ― ${r.folder}${r.exists ? "" : "(見つかりません)"}`);
        o.value = r.path; o.selected = r.current;
        return o;
      });
      if (!data.dbs.some((r) => r.current)) {
        const o = node("option", "", data.current ? `(一覧に無い DB: ${data.current})` : "(選んでください)");
        o.value = ""; o.selected = true; o.disabled = true;
        opts.unshift(o);
      }
      select.replaceChildren(...opts);
    }
    if (clear) clear.disabled = !data.current;
  };
  const anchor = list || select;
  if (anchor && anchor.dataset.wired !== "1") {
    anchor.dataset.wired = "1";
    if (list) list.addEventListener("click", async (event) => {
      const forget = event.target.closest("[data-forget]");
      if (forget) {
        try { const res = await forgetDb(forget); if (res) draw(res); }
        catch (err) { toastError(err); }
        return;
      }
      const pick = event.target.closest("[data-open]");
      if (!pick || pick.dataset.current) return;
      try { await openDb(pick.dataset.open); } catch (err) { toastError(err); }
    });
    if (clear) {
      clear.addEventListener("click", async () => {
        try { await clearDb(); } catch (err) { toastError(err); }
      });
    }
    if (select) {
      select.addEventListener("change", async () => {
        if (!select.value) return;
        try { await openDb(select.value); } catch (err) { toastError(err); }
      });
    }
  }
  try { draw(await api.get("/api/settings/db-choices")); } catch (err) { toastError(err); }
}
