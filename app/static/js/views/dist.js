/*
  配布設定(設定 → 配布設定。0.30.0)。1 台で決めた設定を「配布設定」フォルダに書き出し、配った先が起動時に読む。
  判断(何を入れるか・パスワード・読み込みの決まり)はサーバ(dbviewer/distribution.py)。ここは選ばせて送るだけ。
*/
import { api } from "../api.js";
import { toast } from "../toast.js";

const el = {};

function render(d) {
  el.distState.textContent = d.exists ? "あり" : "なし";
  el.distState.className = `st st--${d.exists ? "ok" : "warn"}`;
  el.distMeta.textContent = d.exists
    ? `${d.created_at} に ${d.created_on} で作成`
    : "まだありません。下で書き出すと、ツールのフォルダの直下に「配布設定」フォルダができます。";
  el.distBundleWrap.hidden = !d.bundle.length;
  el.distBundle.replaceChildren(...d.bundle.map((c) => {
    const tr = document.createElement("tr");
    for (const text of [c.label, c.value]) {
      const td = document.createElement("td");
      td.className = "t";
      td.textContent = text;
      tr.appendChild(td);
    }
    return tr;
  }));
  el.distPath.textContent = d.path;
  el.distApplied.textContent = d.applied_at ? `この PC が最後に読み込んだ(書き出した)のは ${d.applied_at}。` : "";

  const legend = el.distItems.querySelector("legend");
  el.distItems.replaceChildren(legend, ...d.items.map((it) => row(it, "data-dist-item", it.key,
    it.value, it.here ? "この PC の中の場所です(配った先に同じ場所が無いと使えません)" : "")));
  const c = d.contents;
  const legend2 = el.distContents.querySelector("legend");
  const note = !c.exists ? "まだありません"
    : c.here ? "この PC の中にあります。配った先からは見えないので、中身を入れてください"
      : "共有フォルダにあります。「設定ファイルの場所」だけで足ります";
  const box = row({ label: c.label, default: c.default, set: c.exists }, "data-dist-contents", "1", c.file, note);
  el.distContents.replaceChildren(legend2, box);
}

function row(it, attr, key, value, note) {
  const label = document.createElement("label");
  if (!it.set) label.className = "is-unset";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.setAttribute(attr, key);
  cb.checked = !!it.default;
  cb.disabled = !it.set;
  const name = document.createElement("span");
  name.textContent = it.label;
  const v = document.createElement("span");
  v.className = "v";
  v.textContent = value;
  if (note) {
    const n = document.createElement("span");
    n.className = it.set && note.startsWith("この PC") ? "here" : "";
    n.textContent = ` ── ${note}`;
    v.appendChild(n);
  }
  label.append(cb, name, v);
  return label;
}

async function load() {
  try {
    render((await api.get("/api/dist/state")).dist);
  } catch (err) {
    el.distMeta.textContent = err.message;
  }
}

async function send(path, body) {
  el.distWhy.hidden = true;
  try {
    const out = await api.post(path, { ...body, password: el.distPassword.value });
    el.distPassword.value = "";
    render(out.dist);
    toast(out.message, "ok");
  } catch (err) {
    el.distWhy.hidden = false;
    el.distWhy.textContent = err.message;
  }
}

export function start() {
  for (const id of ["distState", "distMeta", "distBundleWrap", "distBundle", "distPath", "distApplied", "distItems",
    "distContents", "distPassword", "distWhy", "distExport", "distReapply", "distRemove"]) {
    el[id] = document.getElementById(id);
  }
  if (!el.distExport) return;
  const checked = (attr) => [...document.querySelectorAll(`[${attr}]`)].filter((x) => x.checked)
    .map((x) => x.getAttribute(attr));
  el.distExport.addEventListener("click", () => send("/api/dist/export",
    { items: checked("data-dist-item"), contents: checked("data-dist-contents").length > 0 }));
  el.distReapply.addEventListener("click", () => send("/api/dist/reapply", {}));
  el.distRemove.addEventListener("click", () => send("/api/dist/remove", {}));
  load();
}
