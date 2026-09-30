/*
  設定画面。判断はサーバが済ませてある。ここは受け取ったものを並べるだけ。
*/

import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import * as tabs from "../tabs.js";

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
  el.progNo.value = String(state.prog_no);
  el.online.checked = !!state.online;
  el.serverDir.value = state.server_dir;
  el.localDir.value = state.local_dir;
  el.dbFile.value = state.db_file;
  el.dbFile.placeholder = state.db_file_default;
  showReal(el.serverDirReal, state.server_dir_real);
  showReal(el.serverDirDefault, state.server_dir_default, "空なら既定: ");
  showReal(el.localDirReal, state.local_dir_real);
  showReal(el.dbPath, state.db_path);
  el.refDir.value = state.ref_dir;
  el.reportDir.value = state.report_dir;
  el.exportDir.value = state.export_dir;
  showReal(el.refDirReal, state.ref_dir_real);
  showReal(el.reportDirReal, state.report_dir_real);
  showReal(el.exportDirReal, state.export_dir_real);
  el.accessLog.checked = !!state.access_log;
  el.reloginMinutes.value = state.relogin_minutes;
  el.accdbSource.value = state.accdb_source;
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
  for (const button of document.querySelectorAll("[data-browse]")) {
    button.addEventListener("click", () => {
      browseTarget = document.getElementById(button.dataset.browse);
      browseFile = button.dataset.browseFile === "1" || button.dataset.browse === "accdbSource";
      browser.pick.hidden = browseFile;
      const label = document.querySelector(`label[for="${button.dataset.browse}"]`);
      browser.for.textContent = label ? label.textContent : "";
      browser.dialog.showModal();
      browseTo(browseTarget.value);
    });
  }
  browser.rows.addEventListener("click", (event) => {
    const dir = event.target.closest("tr[data-dir]");
    if (dir) { browseTo(dir.dataset.dir); return; }
    const file = event.target.closest("tr[data-file]");
    if (file && browseFile && browseTarget) {
      browseTarget.value = file.dataset.file;
      browser.dialog.close();
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
    if (browseTarget) browseTarget.value = browser.path.value;
    browser.dialog.close();
    toast("フォルダを入れました。「保存」を押すと効きます。", "info");
  });
}

export function start(state) {
  for (const id of ["statusGrid", "progNo", "online", "serverDir", "serverDirReal", "serverDirDefault",
                    "localDir", "localDirReal", "dbFile", "dbPath",
                    "refDir", "refDirReal", "reportDir", "reportDirReal", "exportDir", "exportDirReal",
                    "accessLog", "reloginMinutes", "accdbSource",
                    "saveSource", "savePaths", "saveBehavior", "saveAccess", "refresh", "settingsTabs"]) {
    el[id] = document.getElementById(id);
  }
  LEVEL_LABEL = state.level_label || {};
  tabs.attachAll();
  startBrowser();

  el.refresh.addEventListener("click", refresh);
  el.saveSource.addEventListener("click", () => save({
    prog_no: Number(el.progNo.value), online: el.online.checked,
    server_dir: el.serverDir.value, local_dir: el.localDir.value, db_file: el.dbFile.value,
  }));
  el.savePaths.addEventListener("click", () => save({
    ref_dir: el.refDir.value, report_dir: el.reportDir.value, export_dir: el.exportDir.value,
  }));
  el.saveBehavior.addEventListener("click", () => save({
    access_log: el.accessLog.checked, relogin_minutes: Number(el.reloginMinutes.value || 0),
  }));
  el.saveAccess.addEventListener("click", () => save({ accdb_source: el.accdbSource.value }));

  el.statusGrid.addEventListener("click", (event) => {
    const go = event.target.closest("[data-goto]");
    if (go && el.settingsTabs) tabs.select(el.settingsTabs, go.dataset.goto);
  });
  render(state);
}
