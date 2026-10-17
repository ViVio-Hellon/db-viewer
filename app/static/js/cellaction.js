/*
  cellaction.js — マスを押したときにすること(かんたん設定の「クリック」。0.12.0)

    copy … マスの値をコピーする(貼り付けられる)
    url  … URL を開く。URL の決まりが空なら値そのもの、{値} があればそこへ、無ければ末尾に値を付ける

  一覧・閲覧・マスタ管理で同じ。開けるのは http(s) だけ(デスクトップ版は外枠が既定のブラウザで開く)。
  決まりはサーバの `simple_table.click_url` と同じ。
*/
import { toast } from "./toast.js";
import { openExternal } from "./desktop.js";

const URL_MARK = "{値}";
const URL_OK = /^https?:\/\/\S+$/i;

/** URL を開く列の行き先。開けなければ ""。 */
export function clickUrl(spec, value) {
  const text = String(value ?? "").trim();
  if (!spec || spec.click !== "url" || !text) return "";
  if (!spec.url) return URL_OK.test(text) ? text : "";
  const enc = encodeURIComponent(text);
  return spec.url.includes(URL_MARK) ? spec.url.split(URL_MARK).join(enc) : spec.url + enc;
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
export function actionHint(spec, value) {
  if (!spec) return "";
  if (spec.click === "copy") return "押すとコピー";
  const url = clickUrl(spec, value);
  return url ? `押すと開く: ${readable(url)}` : "";
}

/** マスに印を付けるか(値が空・開けない URL なら付けない)。 */
export function isActive(spec, value) {
  if (!spec || String(value ?? "").trim() === "") return false;
  return spec.click === "copy" || (spec.click === "url" && clickUrl(spec, value) !== "");
}

export async function runAction(spec, value) {
  const text = String(value ?? "");
  if (!spec || !text.trim()) return;
  if (spec.click === "copy") {
    if (await copyText(text)) toast(`コピーしました: ${text.length > 40 ? `${text.slice(0, 40)}…` : text}`, "ok");
    else toast("コピーできませんでした。", "warn");
    return;
  }
  if (spec.click === "url") {
    const url = clickUrl(spec, text);
    if (!url) { toast("この値は URL として開けません(http:// か https:// で始まるものだけ)。", "warn"); return; }
    openExternal(url);
    toast(`開きました: ${readable(url)}`, "info");
  }
}

/** 詳細・行の窓に置くボタン(キーボードでも押せる)。 */
export function actionButton(spec, value) {
  if (!isActive(spec, value)) return null;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn btn--find dt__act";
  b.textContent = spec.click === "copy" ? "コピー" : "開く";
  b.title = actionHint(spec, value);
  b.addEventListener("click", () => runAction(spec, value));
  return b;
}
