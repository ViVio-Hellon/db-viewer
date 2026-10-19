/*
  addcolumn.js — 「列を追加」の窓(一覧・閲覧で同じもの)

  窓は初めて開くときに作る(画面ごとに HTML を書かない)。決まり(名前・種類・レベル・控え)は
  サーバ(`master_admin.add_column`)が見る。断り(名前が重なる等)は窓の中に出して、直してもう一度押せる。

    openAddColumn({ table, url, kinds, settingTable, onDone })
      url          … POST 先(/api/list/add-column)。送るのは {table, name, kind, fill}
      kinds        … [[値, 呼び名], …](並びのまま出す。既定は "text")
      settingTable … 一緒に行を足す設定の表(前もって知らせる)。無ければ ""
      onDone(res)  … 足せたとき(res.message, res.column, res.setting_table …)
*/
import { api } from "./api.js";
import { toastError } from "./toast.js";

const DEFAULT_KINDS = [["text", "文字"], ["integer", "整数"], ["number", "数値(小数も)"]];
let dlg = null;
let current = null;

function build() {
  dlg = document.createElement("dialog");
  dlg.className = "browser coldlg";
  dlg.setAttribute("aria-labelledby", "acTitle");
  dlg.innerHTML = `
    <form method="dialog" class="browser__head">
      <b id="acTitle">列を追加</b>
      <button class="btn btn--danger" value="cancel">やめる</button>
    </form>
    <div class="colform">
      <label for="acName">列名</label>
      <input class="input" id="acName" maxlength="64" spellcheck="false" autocomplete="off" placeholder="例: 部署">
      <span>種類</span>
      <div class="opts" id="acKinds" role="radiogroup" aria-label="種類"></div>
      <label for="acFill">いまある行に入れる値</label>
      <input class="input" id="acFill" spellcheck="false" autocomplete="off" placeholder="空のままでよい">
    </div>
    <ul class="why colnotes" id="acNotes"></ul>
    <p class="status status--ng" id="acProblem" hidden></p>
    <div class="btn-row" style="margin-top:8px">
      <button class="btn btn--commit" type="button" id="acRun">列を追加する</button>
    </div>`;
  document.body.appendChild(dlg);
  const run = () => submit();
  dlg.querySelector("#acRun").addEventListener("click", run);
  for (const id of ["#acName", "#acFill"]) {
    dlg.querySelector(id).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } });
  }
}

export function openAddColumn({ table, url, kinds, settingTable = "", onDone }) {
  if (!table) return;
  if (!dlg) build();
  current = { table, url, onDone };
  dlg.querySelector("#acTitle").textContent = `[${table}] に列を追加`;
  dlg.querySelector("#acName").value = "";
  dlg.querySelector("#acFill").value = "";
  dlg.querySelector("#acKinds").replaceChildren(...(kinds && kinds.length ? kinds : DEFAULT_KINDS).map(([k, label]) => {
    const lab = document.createElement("label"); lab.className = "checkline";
    const r = document.createElement("input"); r.type = "radio"; r.name = "acKind"; r.value = k; r.checked = k === "text";
    lab.append(r, document.createTextNode(` ${label}`));
    return lab;
  }));
  const notes = ["いちばん右に足します。いまある行は空(または下の値)になります。",
                 "足す前に本体の控えを取ります(設定 → この端末 のローカル領域の backup)。"];
  if (settingTable) {
    notes.unshift(`[${settingTable}] にも同じ名前の行を足します。入力の種類などは 設定 → かんたん設定 で決められます。`);
  }
  dlg.querySelector("#acNotes").replaceChildren(...notes.map((t) => { const li = document.createElement("li"); li.textContent = t; return li; }));
  dlg.querySelector("#acProblem").hidden = true;
  dlg.showModal();
  dlg.querySelector("#acName").focus();
}

async function submit() {
  if (!current) return;
  const body = {
    table: current.table, name: dlg.querySelector("#acName").value,
    kind: dlg.querySelector("#acKinds input:checked")?.value || "text", fill: dlg.querySelector("#acFill").value,
  };
  const problem = dlg.querySelector("#acProblem");
  try {
    const res = await api.post(current.url, body);
    dlg.close();
    if (current.onDone) await current.onDone(res);
  } catch (err) {
    if (err.status && err.status < 500) { problem.hidden = false; problem.textContent = err.message; }
    else toastError(err);
  }
}
