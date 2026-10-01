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
