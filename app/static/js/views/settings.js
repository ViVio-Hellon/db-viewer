/*
  設定画面。判断はサーバが済ませてある。ここは受け取ったものを並べるだけ。
*/

import * as dbswitch from "../dbswitch.js";
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";
import * as tabs from "../tabs.js";
import * as jobs from "../jobs.js";
import * as logTab from "./log_tab.js";

const el = {};
let LEVEL_LABEL = {};

function checkRow(check) {
  const row = document.createElement("div");
  row.className = `check check--${check.level}`;
  const dt = document.createElement("dt");
  dt.textContent = check.label;
  const dd = document.createElement("dd");
  const value = document.createElement("span");
  value.className = "v";
  value.textContent = check.value;
  dd.appendChild(value);
  if (check.detail) {
    const why = document.createElement("span");
    why.className = "why";
    why.textContent = check.detail;
    dd.appendChild(why);
  }
  row.append(dt, dd);
  return row;
}

function sectionCard(section) {
  const card = document.createElement("section");
  card.className = "card";
  const head = document.createElement("header");
  const badge = document.createElement("i");
  badge.className = `ib ib--sm ib--${section.badge_tone}`;
  badge.setAttribute("aria-hidden", "true");
  badge.textContent = section.mark;
  const title = document.createElement("h3");
  title.textContent = section.title;
  const level = document.createElement("span");
  level.className = `lv lv--${section.level}`;
  level.textContent = section.label || LEVEL_LABEL[section.level] || section.level;
  head.append(badge, title, level);
  const pad = document.createElement("div");
  pad.className = "pad";
  const list = document.createElement("dl");
  list.className = "checks";
  list.append(...section.checks.map(checkRow));
  pad.appendChild(list);
  if (section.action) {
    const row = document.createElement("div");
    row.className = "btn-row";
    const go = document.createElement("button");
    go.type = "button";
    go.className = "btn btn--find";
    go.dataset.goto = section.action.tab;
    const glyph = document.createElement("span");
    glyph.className = "gl";
    glyph.textContent = "▸";
    go.append(glyph, document.createTextNode(section.action.label));
    row.appendChild(go);
    pad.appendChild(row);
  }
  card.append(head, pad);
  return card;
}

function showReal(node, text, prefix = "→ ") {
  if (!node) return;
  node.hidden = !text;
  node.textContent = text ? `${prefix}${text}` : "";
}

function render(state) {
  LEVEL_LABEL = state.level_label || LEVEL_LABEL;
  el.statusGrid.replaceChildren(...state.sections.map(sectionCard));
  // 相対で入れたときだけ、実際に開く場所を出す
  showReal(el.dbPath, state.db_path || "");
  el.refDir.value = state.ref_dir;
  el.exportDir.value = state.export_dir;
  showReal(el.refDirReal, state.ref_dir_real);
  showReal(el.exportDirReal, state.export_dir_real);
  for (const [node, st] of [[el.exportDirState, state.export_dir_state], [el.refDirState, state.ref_dir_state]]) {
    if (node && st) { node.className = `lv lv--${st.level}`; node.textContent = st.text; }
  }
  for (const r of document.querySelectorAll("input[name=exportFormat]")) r.checked = r.value === state.export_format;
  if (el.exportEncoding) el.exportEncoding.value = state.export_encoding;
  el.accessLog.checked = !!state.access_log;
  el.reloginMinutes.value = state.relogin_minutes;
  el.accdbSource.value = state.accdb_source;
  el.accdbTarget.value = state.accdb_target || "";
  showTargetDefault();
  loadDbFiles();
  dbswitch.renderList(el.dbRecent, { select: el.dbSelect, addBox: el.dbAddBox, where: el.dbListWhere, clear: el.dbClear });
  logTab.render(state.log);
  if (el.settingsTabs) tabs.setBadges(el.settingsTabs, state.tab_badges);
}

async function refresh() {
  try { render(await api.get("/api/settings/state")); } catch (err) { toastError(err); }
}

async function save(body) {
  try {
    const state = await api.post("/api/settings/save", body);
    render(state);
    toast(state.message || "保存しました", "ok");
  } catch (err) { toastError(err); }
}

/* ---------------- フォルダの参照(サーバ側のフォルダを辿る) ---------------- */
const browser = {};
let browseTarget = null;
let browseFile = false;

async function browseTo(path) {
  try {
    const view = await api.get(`/api/fs/list?path=${encodeURIComponent(path || "")}`
                               + (browseFile ? "&access=1" : ""));
    browser.path.value = view.path;
    browser.note.hidden = !view.message;
    browser.note.textContent = view.message;
    browser.note.className = `status status--${view.readable ? "ok" : "ng"}`;
    browser.up.disabled = !view.parent;
    browser.upPath = view.parent;
    browser.pick.disabled = !view.readable;
    const rows = [];
    const source = view.readable ? [...view.dirs, ...view.files] : view.roots;
    for (const entry of source) {
      const tr = document.createElement("tr");
      if (entry.is_dir) tr.dataset.dir = entry.path;
      else tr.dataset.file = entry.path;
      tr.dataset.rowAction = "1";
      if (entry.name === view.picked) tr.setAttribute("aria-current", "true");
      const td = document.createElement("td");
      const glyph = document.createElement("span");
      glyph.className = "gl";
      glyph.textContent = entry.is_dir ? "▸" : "▪";
      td.append(glyph, document.createTextNode(entry.name));
      tr.appendChild(td);
      rows.push(tr);
    }
    browser.rows.replaceChildren(...rows);
  } catch (err) { toastError(err); }
}

function startBrowser() {
  for (const [key, id] of [["dialog", "browser"], ["path", "browsePath"],
                           ["rows", "browseRows"], ["note", "browseNote"],
                           ["go", "browseGo"], ["up", "browseUp"],
                           ["pick", "browsePick"], ["for", "browseFor"]]) {
    browser[key] = document.getElementById(id);
  }
  if (!browser.dialog) return;
  // 「参照…」は後から足したボタン(かんたん設定の場所の「フォルダを選ぶ…」。0.26.0)でも効くよう、まとめて受ける
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-browse]");
    if (!button) return;
    browseTarget = document.getElementById(button.dataset.browse);
    if (!browseTarget) return;
    // data-browse-file="either": ファイルを押しても、「このフォルダを選ぶ」でもよい(設定ファイル。0.22.0)
    const either = button.dataset.browseFile === "either";
    browseFile = either || button.dataset.browseFile === "1" || button.dataset.browse === "accdbSource";
    browser.pick.hidden = browseFile && !either;
    const label = document.querySelector(`label[for="${button.dataset.browse}"]`);
    browser.for.textContent = button.dataset.browseFor || (label ? label.textContent : "");
    browser.dialog.showModal();
    browseTo(browseTarget.dataset.keepName === "1" ? folderPart(browseTarget.value) : browseTarget.value);
  });
  browser.rows.addEventListener("click", (event) => {
    const dir = event.target.closest("tr[data-dir]");
    if (dir) { browseTo(dir.dataset.dir); return; }
    const file = event.target.closest("tr[data-file]");
    if (file && browseFile && browseTarget) {
      browseTarget.value = file.dataset.file;
      browser.dialog.close();
      // 「表を持ってくる」の欄なら、選んだらすぐ中を読む
      if (browseTarget.id === "bringPath") lookBring();
      // DB ファイルの欄なら、そのフォルダのファイルを見直す(保存はまだ)
      // DB ファイルの欄なら、選んだらすぐその DB に切り替える(保存を押す手間を省く)
      if (browseTarget.id === "dbPathText") openDb(file.dataset.file);
      return;
    }
    if (file) browseTo(file.dataset.file);
  });
  browser.go.addEventListener("click", () => browseTo(browser.path.value));
  browser.path.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); browseTo(browser.path.value); }
  });
  browser.up.addEventListener("click", () => browseTo(browser.upPath));
  browser.pick.addEventListener("click", () => {
    if (browseTarget && browseTarget.dataset.keepName === "1") {
      // 場所の決まり: フォルダだけ入れ替え、ファイル名の所({作業NO}_{作業名} など)は残す
      const folder = browser.path.value.replace(/[\\/]+$/, "");
      const sep = folder.includes("\\") || !folder.includes("/") ? "\\" : "/";
      browseTarget.value = `${folder}${sep}${namePart(browseTarget.value) || "{値}"}`;
      browseTarget.dispatchEvent(new Event("input", { bubbles: true }));
      browseTarget.dispatchEvent(new Event("change", { bubbles: true }));
      browser.dialog.close();
      toast("フォルダを入れました。「列の設定を保存」を押すと効きます。", "info");
      return;
    }
    if (browseTarget) browseTarget.value = browser.path.value;
    browser.dialog.close();
    toast("フォルダを入れました。「保存」を押すと効きます。", "info");
  });
}

/** 場所の決まりのフォルダの所 / ファイル名の所(最後の \ か / で分ける)。 */
function folderPart(text) {
  const t = String(text || "").trim();
  const cut = Math.max(t.lastIndexOf("\\"), t.lastIndexOf("/"));
  return cut > 0 ? t.slice(0, cut) : "";
}
function namePart(text) {
  const t = String(text || "").trim();
  const cut = Math.max(t.lastIndexOf("\\"), t.lastIndexOf("/"));
  return cut >= 0 ? t.slice(cut + 1) : t;
}

/* ---------------- Access 取り込み(長時間処理は jobs.js が見張る) ---------------- */
let unwatch = null;

function showJob(job) {
  if (!el.accessJob) return;
  const mine = job && job.kind === "accdb_import";
  el.accessJob.hidden = !mine;
  if (!mine) return;
  el.accessJobLabel.textContent = job.label;
  el.accessJobState.className = `st st--${job.state_kind}`;
  el.accessJobState.textContent = job.state_label;
  el.accessJobPct.textContent = `${job.pct}%`;
  el.accessJobBar.style.width = `${job.pct}%`;
  el.accessJobMessage.textContent = job.message || "";
  const done = !job.running;
  el.accessJobResult.hidden = !done;
  el.accessJobResult.className = `status status--${job.ok ? "ok" : "ng"}`;
  el.accessJobResult.textContent = done ? (job.ok ? job.summary : job.error) : "";
  // 変換できたら、その .sqlite3 をすぐ開けるように
  if (el.openConverted) el.openConverted.hidden = !(done && job.ok);
}

function onJobs(state) {
  const job = state.running[0] || state.recent[0];
  showJob(job);
  el.runAccess.disabled = !!state.busy;
  if (!state.busy && lastBusy) refresh();
  lastBusy = !!state.busy;
}
let lastBusy = false;

/* ---------------- DB ファイルのあるフォルダのファイル ---------------- */
// 画面で入れている DB ファイルのフォルダ(保存前でも)。空ならサーバが決める(いまの DB のフォルダ)
function activeFolder() {
  const text = el.dbPathText.value.trim().replace(/^"|"$/g, "");
  const cut = Math.max(text.lastIndexOf("\\"), text.lastIndexOf("/"));
  return cut > 0 ? text.slice(0, cut) : "";
}

async function loadDbFiles() {
  if (!el.dbFiles) return;
  try {
    const d = await api.get(`/api/settings/db-files?path=${encodeURIComponent(activeFolder())}`);
    el.dbFilesFolder.textContent = `フォルダ: ${d.folder}`;
    el.dbFilesNote.hidden = !d.message;
    el.dbFilesNote.className = `status status--${d.exists ? "warn" : "ng"}`;
    el.dbFilesNote.textContent = d.message;
    el.dbFiles.replaceChildren(...d.files.map(fileRow));
  } catch (err) { toastError(err); }
}

function fileRow(f) {
  const li = document.createElement("li");
  li.className = `dbfile dbfile--${f.kind}${f.current ? " is-current" : ""}`;
  const pill = document.createElement("span");
  pill.className = `lv lv--${f.kind === "open" ? "ok" : f.kind === "convert" ? "warn" : "info"}`;
  pill.textContent = f.kind === "open" ? (f.current ? "いま開いている" : "開けます")
    : f.kind === "convert" ? "変換すると開けます" : "対象外";
  const name = document.createElement("b");
  name.textContent = f.name;
  const note = document.createElement("small");
  note.textContent = `${f.note} ・ ${f.mtime} ・ ${(f.size / 1024).toLocaleString(undefined, { maximumFractionDigits: 0 })} KB`;
  li.append(pill, name, note);
  if (f.kind === "open" && !f.current) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn btn--commit"; b.textContent = "これを開く";
    b.addEventListener("click", () => openDb(f.path));
    li.appendChild(b);
  } else if (f.kind === "convert") {
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn btn--find"; b.textContent = "Access 取り込みへ";
    b.addEventListener("click", () => {
      el.accdbSource.value = f.path;
      el.accdbTarget.value = f.target;
      showTargetDefault();
      if (el.settingsTabs) tabs.select(el.settingsTabs, "access");
    });
    li.appendChild(b);
  }
  return li;
}

// 切り替えたら画面ごと読み直す(上の帯の DB 名も変わる)。だめなら理由を出して今のまま
async function openDb(path) {
  try { await dbswitch.openDb(path); } catch (err) { toastError(err); }
}

function showTargetDefault() {
  if (!el.accdbTargetDefault) return;
  const src = el.accdbSource.value.trim();
  const stem = src ? src.split(/[\\/]/).pop().replace(/\.(accdb|mdb)$/i, "") : "(元の名前)";
  const folder = activeFolder() || "(接続先フォルダ)";
  el.accdbTargetDefault.textContent = `${folder}${/[\\/]$/.test(folder) ? "" : (folder.includes("\\") ? "\\" : "/")}${stem}.sqlite3`;
}

async function runAccess() {
  try {
    const res = await api.post("/api/settings/accdb-import",
                               { accdb_source: el.accdbSource.value, accdb_target: el.accdbTarget.value });
    toast(res.message, "ok");
    showJob(res.job);
    jobs.refresh();
  } catch (err) { toastError(err); jobs.refresh(); }
}

/* ================================================================
   Access で作った表を持ってくる(`dbviewer/table_bring.py`)
   python-web-tools の設定画面から移植。

   変換したファイル(または Access)の中を見て、本体に**無い表だけ**を選ばせる。
   もうある表は「入れ替える・作り直す」だけ選べる。
   ================================================================ */
const bring = {};

function renderBring(plan) {
  if (bring.dest) {
    bring.dest.hidden = !plan.dest_note;
    bring.dest.textContent = plan.dest_note || "";
  }
  bring.converted.hidden = !plan.converted;
  bring.converted.textContent = plan.converted || "";
  bring.note.hidden = !plan.message;
  bring.note.textContent = plan.message || "";
  bring.note.className = `status status--${!plan.ok ? "ng" : plan.warn ? "warn" : "ok"}`;
  const rows = (plan.tables || []).map((t) => {
    const tr = document.createElement("tr");
    const pick = document.createElement("td");
    if (t.exists && t.can_refresh) {
      // もうある表。Access の最新に入れ替える(列が変わっていれば作り直す)ことだけ選べる。
      // 消して入れ直す操作なので、既定では選ばない
      const rebuild = t.action === "rebuild";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = t.name;
      box.dataset.refresh = "1";
      box.dataset.action = t.action;
      box.dataset.loses = (t.import_loses || []).join("・");
      box.checked = false;
      box.addEventListener("change", updateBringRun);
      const label = document.createElement("label");
      label.className = "why";
      label.append(box, rebuild ? " 作り直す" : " 入れ替える");
      pick.appendChild(label);
    } else if (t.exists) {
      pick.textContent = "もうある";
      pick.className = "why";
      tr.title = t.refresh_why || "本体にもう同じ名前の表があります";
    } else {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = t.name;
      // 文字化けの疑いがある表は、見比べてから自分で選んでもらう
      box.checked = !t.suspect;
      box.addEventListener("change", updateBringRun);
      pick.appendChild(box);
    }
    const name = document.createElement("td");
    name.textContent = t.name;
    const count = document.createElement("td");
    count.className = "num";
    count.textContent = t.rows.toLocaleString();
    if (t.suspect) {
      const warn = document.createElement("div");
      warn.className = "why";
      warn.textContent = `⚠ 化けの疑い ${t.suspect}行`;
      count.appendChild(warn);
    }
    const cols = document.createElement("td");
    cols.className = "why";
    cols.textContent = t.columns.join(", ");
    if (t.exists) {
      const note = document.createElement("div");
      const added = t.added_columns || [];
      const removed = t.removed_columns || [];
      const loses = t.import_loses || [];
      note.textContent = t.can_refresh
        ? `いま ${t.current_rows.toLocaleString()}行 → Access ${t.rows.toLocaleString()}行`
          + (t.keeps ? `。${t.keeps}` : "")
          + (added.length ? `。Access で増えた列を足します: ${added.join(", ")}` : "")
          + (removed.length
            ? `。Access に無い列があるので作り直します(消える列: ${removed.join(", ")})。`
              + "今の表は「表名_作り直す前_日時」の名前で残します" : "")
          + (loses.length
            ? `。⚠ 設定(_設定・フィルタ)で使っている列が消えます: ${loses.join(", ")}` : "")
        : `入れ替えられません: ${t.refresh_why}`;
      cols.prepend(note);
    }
    tr.append(pick, name, count, cols);
    // 持ってこられる表を上に、入れ替えられる表を次に、どちらもできない表を下に
    tr.dataset.order = !t.exists ? "0" : t.can_refresh ? "1" : "2";
    return tr;
  }).sort((a, b) => Number(a.dataset.order) - Number(b.dataset.order));
  bring.rows.replaceChildren(...rows);
  bring.list.hidden = rows.length === 0;
  updateBringRun();
}

function chosenTables() {
  return [...bring.rows.querySelectorAll("input[type=checkbox]:checked:not([data-refresh])")]
    .map((box) => box.value);
}

function chosenRefresh() {
  return [...bring.rows.querySelectorAll("input[type=checkbox][data-refresh]:checked")]
    .map((box) => box.value);
}

function updateBringRun() {
  bring.run.disabled = chosenTables().length === 0;
  if (bring.refresh) bring.refresh.disabled = chosenRefresh().length === 0;
}

function showDone(result) {
  renderBring(result.plan);
  toast(result.message, "ok");
  bring.note.hidden = false;
  bring.note.className = "status status--ok";
  bring.note.textContent = `${result.message}(控え: ${result.backup})`;
  refresh();
}

async function runRefresh() {
  const tables = chosenRefresh();
  if (!tables.length) return;
  const boxes = [...bring.rows.querySelectorAll("input[type=checkbox][data-refresh]:checked")];
  const swap = boxes.filter((b) => b.dataset.action !== "rebuild").map((b) => b.value);
  const rebuild = boxes.filter((b) => b.dataset.action === "rebuild").map((b) => b.value);
  const loses = boxes.filter((b) => b.dataset.loses).map((b) => `${b.value}(${b.dataset.loses})`);
  // **消して入れ直す。** 控えは取るが、押す前に何が起きるかを言う
  if (!await confirmBox(
    (swap.length ? `中身を Access の中身に入れ替える表:\n${swap.join("、")}\n\n` : "")
    + (rebuild.length ? `Access の定義で作り直す表(今の表は名前を変えて残します):\n`
                        + `${rebuild.join("、")}\n\n` : "")
    + (loses.length ? `⚠ 設定で使っている列が消えます: ${loses.join("、")}\n\n` : "")
    + "本体にある今の行は消えます(書く前に控えを取ります)。"
    + "ほかの端末にもすぐ見えます。よろしいですか？", { ok: "入れ替える", danger: true })) return;
  try {
    showDone(await api.post("/api/settings/table-refresh",
                            { path: bring.path.value.trim(), tables }));
  } catch (err) {
    if (err.body && err.body.plan) renderBring(err.body.plan);
    toastError(err);
  }
}

async function lookBring() {
  const path = bring.path.value.trim();
  if (!path) { renderBring({ ok: false, message: "持ってくるファイルを選んでください。" }); return; }
  try {
    renderBring(await api.get(`/api/settings/table-bring/plan?path=${encodeURIComponent(path)}`));
  } catch (err) {
    toastError(err);
  }
}

async function runBring() {
  const tables = chosenTables();
  if (!tables.length) return;
  try {
    showDone(await api.post("/api/settings/table-bring",
                            { path: bring.path.value.trim(), tables }));
  } catch (err) {
    if (err.body && err.body.plan) renderBring(err.body.plan);
    toastError(err);
  }
}

/** ファイルを落とせる場所にする。落ちたら受け取って、すぐ中を読む。 */
function acceptDrop(zone, before) {
  if (!zone) return;
  const over = (on) => zone.classList.toggle("is-over", on);
  zone.addEventListener("dragover", (event) => {
    if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
    event.preventDefault();
    over(true);
  });
  zone.addEventListener("dragleave", () => over(false));
  zone.addEventListener("drop", async (event) => {
    event.preventDefault();
    over(false);
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    if (before) before();
    await uploadBring(file);
  });
}

async function uploadBring(file) {
  const form = new FormData();
  form.append("file", file, file.name);
  bring.note.hidden = false;
  bring.note.className = "status";
  bring.note.textContent = `${file.name} を受け取っています…`;
  try {
    const got = await api.postForm("/api/settings/table-bring/upload", form);
    bring.path.value = got.path;
    await lookBring();
  } catch (err) {
    bring.note.className = "status status--ng";
    bring.note.textContent = err.message;
    toastError(err);
  }
}

function startBring() {
  for (const [key, id] of [["dialog", "bringDialog"], ["open", "bringOpen"],
                           ["path", "bringPath"], ["look", "bringLook"],
                           ["note", "bringNote"], ["list", "bringList"],
                           ["rows", "bringRows"], ["run", "bringRun"],
                           ["converted", "bringConverted"], ["drop", "bringDrop"],
                           ["dest", "bringDest"], ["refresh", "bringRefresh"],
                           ["openDrop", "bringOpenDrop"]]) {
    bring[key] = document.getElementById(id);
  }
  if (!bring.dialog) return;
  bring.open.addEventListener("click", () => bring.dialog.showModal());
  // **落とすだけで読む。** ダイアログの中の枠にも、設定画面の段にも落とせる
  acceptDrop(bring.drop);
  acceptDrop(bring.openDrop, () => { if (!bring.dialog.open) bring.dialog.showModal(); });
  bring.look.addEventListener("click", lookBring);
  bring.path.addEventListener("change", lookBring);
  bring.path.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); lookBring(); }
  });
  bring.run.addEventListener("click", runBring);
  if (bring.refresh) bring.refresh.addEventListener("click", runRefresh);
}

export function start(state) {
  for (const id of ["statusGrid", "dbPathText", "dbPath", "dbRecent", "dbSelect", "dbClear", "dbAddBox", "dbListWhere",
                    "refDir", "refDirReal", "exportDir", "exportDirReal", "exportDirState", "refDirState", "exportEncoding",
                    "saveExport", "openExport", "saveRef",
                    "accessLog", "reloginMinutes", "accdbSource", "accdbTarget", "accdbTargetDefault", "openConverted",
                    "dbFiles", "dbFilesFolder", "dbFilesNote", "dbFilesReload",
                    "saveSource", "saveBehavior", "runAccess", "refresh", "settingsTabs",
                    "accessJob", "accessJobLabel", "accessJobState", "accessJobPct", "accessJobBar",
                    "accessJobMessage", "accessJobResult"]) {
    el[id] = document.getElementById(id);
  }
  LEVEL_LABEL = state.level_label || {};
  tabs.attachAll();
  startBrowser();
  startBring();
  logTab.start(state, save);

  el.refresh.addEventListener("click", refresh);
  el.saveSource.addEventListener("click", () => openDb(el.dbPathText.value));
  el.saveExport.addEventListener("click", () => save({
    export_dir: el.exportDir.value,
    export_format: document.querySelector("input[name=exportFormat]:checked")?.value || "csv",
    export_encoding: el.exportEncoding.value,
  }));
  el.saveRef.addEventListener("click", () => save({ ref_dir: el.refDir.value }));
  el.openExport.addEventListener("click", async () => {
    try { await api.post("/api/export/open", {}); } catch (err) { toastError(err); }
  });
  el.saveBehavior.addEventListener("click", () => save({
    access_log: el.accessLog.checked, relogin_minutes: Number(el.reloginMinutes.value || 0),
  }));
  el.runAccess.addEventListener("click", runAccess);
  el.openConverted.addEventListener("click", async () => {
    // 変換した先(保存先が空なら既定の場所)
    const target = el.accdbTarget.value.trim() || el.accdbTargetDefault.textContent;
    await openDb(target);
    if (el.settingsTabs) tabs.select(el.settingsTabs, "source");
  });
  el.dbFilesReload.addEventListener("click", loadDbFiles);
  el.dbPathText.addEventListener("change", () => { loadDbFiles(); showTargetDefault(); });
  el.accdbSource.addEventListener("change", showTargetDefault);
  if (unwatch) unwatch();
  unwatch = jobs.subscribe(onJobs);

  el.statusGrid.addEventListener("click", (event) => {
    const go = event.target.closest("[data-goto]");
    if (go && el.settingsTabs) tabs.select(el.settingsTabs, go.dataset.goto);
  });
  render(state);
}
