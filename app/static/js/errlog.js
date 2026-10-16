/*
  errlog.js — 画面の中(ブラウザ)で起きたエラーを記録へ送る

  サーバのエラーはサーバが残す。ここが拾うのは **JavaScript の不具合**
  ── ボタンが効かない・表が出ない、の正体はたいていこちらで、これまでは
  開発者ツールを開かないかぎり何も残らなかった。

  送り先は `POST /api/log/client`(記録番号が返る)。送れなくても黙って諦める
  (サーバが止まっているなら、どのみち残せない)。1 画面で送るのは数件まで ──
  繰り返し起きるエラーで記録を埋めないため。
*/

const MAX_PER_PAGE = 5;
let sent = 0;
const seen = new Set();

// 直前の操作(押したボタン・選んだ欄の**名前**だけ。打った値は送らない)。エラーに添えて
// 「何をしてから壊れたか」を後から追えるようにする(参照: vba-daily-report の「パンくず」)
const MAX_CRUMBS = 15;
const crumbs = [];

function nameOf(node) {
  const text = (node.getAttribute("aria-label") || node.title || node.textContent || "").trim().replace(/\s+/g, " ");
  const label = node.id ? document.querySelector(`label[for="${CSS.escape(node.id)}"]`)?.textContent?.trim() : "";
  return (label || text || node.name || node.id || node.tagName.toLowerCase()).slice(0, 40);
}

function remember(event) {
  const node = event.target.closest?.("button, a, select, input, textarea, [role=tab], th, td");
  if (!node) return;
  const kind = event.type === "change" ? "選んだ" : event.type === "dblclick" ? "ダブルクリック" : "押した";
  const t = new Date();
  crumbs.push(`${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}:${String(t.getSeconds()).padStart(2, "0")} ${kind} ${nameOf(node)}`);
  while (crumbs.length > MAX_CRUMBS) crumbs.shift();
}

/** いまの直前の操作(新しいものが下)。 */
export function recentCrumbs() { return [...crumbs]; }

// 送っても意味のないもの(ブラウザの都合・通信が切れただけ・よそのスクリプト)
const NOISE = [/ResizeObserver loop/i, /^Script error\.?$/i, /Failed to fetch/i,
               /NetworkError/i, /Load failed/i, /AbortError/i];

export function worthReporting(message) {
  const m = String(message || "");
  return !NOISE.some((re) => re.test(m));
}

/**
 * @param {"error"|"rejection"|"caught"} kind
 * @param {Error|any} err
 * @param {{source?:string, line?:number, col?:number, where?:string}} [extra]
 * @returns {Promise<string>} 記録番号(送れなければ "")
 */
export async function report(kind, err, extra = {}) {
  const message = (err && err.message) || String(err ?? "");
  if (!worthReporting(message)) return "";
  const key = `${kind}:${message}:${extra.line || ""}`;
  if (seen.has(key) || sent >= MAX_PER_PAGE) return "";
  seen.add(key);
  sent += 1;
  try {
    const res = await fetch("/api/log/client", {
      method: "POST",
      cache: "no-store",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        "X-Tool-Token": window.APP?.token || "",
        "X-Screen-Id": window.APP?.screenId || "",
      },
      body: JSON.stringify({
        kind,
        message: message.slice(0, 400),
        stack: String((err && err.stack) || "").slice(0, 3000),
        page: location.pathname + location.search,
        crumbs: recentCrumbs(),
        ...extra,
      }),
    });
    const body = await res.json().catch(() => ({}));
    return body.rid || res.headers.get("X-Request-Id") || "";
  } catch {
    return "";
  }
}

/** 画面を移ったら数え直す(nav.js の差し替え)。 */
export function resetCount() { sent = 0; seen.clear(); }

/**
 * 拾われなかったエラーを送る。
 * @param {(rid:string, message:string) => void} notify 送れたら呼ぶ(知らせを出す)
 */
export function install(notify) {
  for (const type of ["click", "dblclick", "change"]) document.addEventListener(type, remember, true);
  window.addEventListener("error", async (event) => {
    // よそ(拡張機能など)のスクリプトのエラーは送らない
    if (event.filename && !event.filename.startsWith(location.origin)) return;
    const err = event.error || { message: event.message };
    const rid = await report("error", err, { source: (event.filename || "").replace(location.origin, ""),
                                            line: event.lineno, col: event.colno });
    if (rid) notify(rid, err.message || String(err));
  });
  window.addEventListener("unhandledrejection", async (event) => {
    const reason = event.reason;
    // サーバの断り・エラーは、サーバがもう残している
    if (reason && reason.name === "ApiError") return;
    const rid = await report("rejection", reason || "(理由なし)");
    if (rid) notify(rid, (reason && reason.message) || String(reason));
  });
}
