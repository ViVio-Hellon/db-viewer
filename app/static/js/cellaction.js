/*
  cellaction.js — マスの値を押したときにすること(かんたん設定の「クリック」。0.12.0・0.13.0)

    copy    … マスの値をコピーする(貼り付けられる)
    open    … 現物を開く。URL は既定のブラウザ、Excel・Word・PDF はその PC の既定のアプリ(サーバが開く)
    preview … アプリの窓の中で見る。PDF・画像・テキスト・Excel・Word は中身を、URL は窓の中の枠に

  開くもの(Excel・Word・PDF・URL)は、値(または「場所」の決まり)から決まる:
    場所が空 → 値そのもの。{値} があればそこへ、{列名} があれば同じ行のその列の値を(0.23.0)、
    どちらも無ければ末尾に値を付ける(URL の決まりなら値は符号化)
    http(s):// で始まれば URL、それ以外はファイルの場所(相対パスは 参照ファイルの起点 から)
    拡張子を書かなければ、サーバがフォルダの中から 全角・半角・大文字小文字を問わず 探す(Excel → Word → PDF の順)

  row = { values: {列名: 値}, rowid } を渡すと {列名} を埋める(サーバへは rowid を送り、サーバが DB の行で埋め直す)。

  一覧で使う。決まりはサーバの `simple_table.click_target` と同じ。
*/
import { api, tokenUrl } from "./api.js";
import { toast, toastError } from "./toast.js";
import { openExternal, openWindow } from "./desktop.js";

const MARK = "{値}";
const URL_OK = /^https?:\/\/\S+$/i;
const SCHEME = /^[A-Za-z][A-Za-z0-9+.-]*:/;
const DRIVE = /^[A-Za-z]:[\\/]|^\\\\/;
const KINDS = {
  ".xlsx": "Excel", ".xlsm": "Excel", ".xls": "Excel", ".csv": "Excel",
  ".docx": "Word", ".docm": "Word", ".doc": "Word", ".pdf": "PDF",
  ".png": "画像", ".jpg": "画像", ".jpeg": "画像", ".gif": "画像", ".bmp": "画像", ".webp": "画像",
  ".txt": "テキスト", ".log": "テキスト",
};

const ROW_MARK = /\{([^{}]+)\}/g;

/** 場所の決まりに書いた {列名}({値} は除く)。 */
export function rowMarks(place) {
  return [...String(place || "").matchAll(ROW_MARK)].map((m) => m[1]).filter((n) => n !== "値");
}

/** 開く列の行き先 → { kind: "url" | "file" | "", target, what }。 */
export function clickTarget(spec, value, row = null) {
  const text = String(value ?? "").trim().replace(/^"|"$/g, "");
  const none = { kind: "", target: "", what: "" };
  if (!spec || !["open", "preview"].includes(spec.click) || !text) return none;
  const place = spec.place || "";
  let target = text;
  if (place) {
    const put = (v) => (URL_OK.test(place) ? encodeURIComponent(v) : v);
    const marks = rowMarks(place);
    if (place.includes(MARK) || marks.length) {
      target = place.split(MARK).join(put(text));
      const values = (row && row.values) || {};
      for (const n of marks) {
        if (n in values) target = target.split(`{${n}}`).join(put(String(values[n] ?? "").trim()));
      }
    } else {
      target = place + put(text);
    }
  }
  if (URL_OK.test(target)) return { kind: "url", target, what: "URL" };
  if (SCHEME.test(target) && !DRIVE.test(target)) return none;
  const m = target.toLowerCase().match(/\.[a-z0-9]+$/);
  const what = (m && KINDS[m[0]]) || "";
  return { kind: "file", target, what: what || "ファイルを名前で探す" };
}

/** 知らせに出す URL(%E7%AE%B1 → 箱。読めなければそのまま)。 */
function readable(url) {
  try { return decodeURI(url); } catch { return url; }
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // クリップボードが使えないとき。選んでコピーする
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) return false;
  }
  return true;
}

/** そのマスで押したときの説明(マスの title・詳細のボタン)。 */
export function actionHint(spec, value, row = null) {
  if (!spec) return "";
  if (spec.click === "copy") return "押すとコピー";
  const t = clickTarget(spec, value, row);
  if (!t.kind) return "";
  const how = spec.click === "open" ? "現物を開く" : "プレビュー";
  return `押すと${how}(${t.what || "ファイル"}): ${t.kind === "url" ? readable(t.target) : t.target}`;
}

/** マスに印を付けるか(値が空・開けない値なら付けない)。 */
export function isActive(spec, value, row = null) {
  if (!spec || String(value ?? "").trim() === "") return false;
  return spec.click === "copy" || clickTarget(spec, value, row).kind !== "";
}

export async function runAction(spec, value, row = null) {
  const text = String(value ?? "");
  if (!spec || !text.trim()) return;
  if (spec.click === "copy") {
    if (await copyText(text)) toast(`コピーしました: ${text.length > 40 ? `${text.slice(0, 40)}…` : text}`, "ok");
    else toast("コピーできませんでした。", "warn");
    return;
  }
  const t = clickTarget(spec, text, row);
  if (!t.kind) { toast("この値は開けません(URL でもファイルの場所でもありません)。", "warn"); return; }
  const rowid = row && row.rowid !== undefined && row.rowid !== null ? String(row.rowid) : "";
  if (spec.click === "preview") {
    // サーバが中身を見せる(URL は窓の中の枠に)。その列に入っている値だけ
    const q = new URLSearchParams({ table: spec.table || "", column: spec.column || "", value: text.trim() });
    if (rowid) q.set("rowid", rowid);
    if (!openWindow(tokenUrl(`/api/file/preview?${q}`), `${spec.column || ""} — ${text.trim()}`)) {
      toast("別の窓を開けませんでした。ポップアップの許可を確認してください。", "warn");
    }
    return;
  }
  if (t.kind === "url" && !rowMarks(spec.place).length) {
    openExternal(t.target);
    toast(`開きました: ${readable(t.target)}`, "info");
    return;
  }
  try {
    const res = await api.post("/api/file/open", { table: spec.table || "", column: spec.column || "", value: text.trim(), rowid });
    if (res.kind === "url") {                     // {列名} を使う URL はサーバが埋めた行き先を開く
      openExternal(res.url);
      toast(`開きました: ${readable(res.url)}`, "info");
      return;
    }
    toast(res.message || "開きました。", "ok");
  } catch (err) { toastError(err, "warn"); }
}

/** 詳細・行の窓に置くボタン(キーボードでも押せる)。 */
export function actionButton(spec, value, row = null) {
  if (!isActive(spec, value, row)) return null;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn--find dt__act";
  b.textContent = { copy: "コピー", open: "開く", preview: "プレビュー" }[spec.click] || "開く";
  b.title = actionHint(spec, value, row);
  b.addEventListener("click", () => runAction(spec, value, row));
  return b;
}
