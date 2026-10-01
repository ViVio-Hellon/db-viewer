/*
  toast.js — 作業を止めない通知

  止める確認(モーダル)は「取り消せない操作」だけに使う。
  確認が多すぎると、慣れて素通りするようになる。
*/

import { report } from "./errlog.js";

const HOST = () => document.getElementById("toasts");

/** 種類ごとの表示時間(ms)。読む量に比例させる。0 は自動で消さない。 */
const LIFETIME = { ok: 4000, info: 5000, warn: 8000, error: 0 };

/**
 * @param {string} message 現場向けの日本語。文言はサーバが持つ
 * @param {"ok"|"info"|"warn"|"error"} kind
 * @param {{label:string, onClick:Function}} [action] 「元に戻す」など
 */
export function toast(message, kind = "info", action = null) {
  const host = HOST();
  if (!host) return;

  const el = document.createElement("div");
  el.className = `toast toast--${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");

  const text = document.createElement("span");
  text.className = "toast__text";
  text.textContent = message;
  el.appendChild(text);

  if (action) {
    const button = document.createElement("button");
    button.className = "btn";
    button.type = "button";
    button.textContent = action.label;
    button.style.marginLeft = "auto";
    button.addEventListener("click", () => { action.onClick(); el.remove(); });
    el.appendChild(button);
  }

  // エラーは自動で消さない代わりに、閉じる手段を必ず出す
  if (!LIFETIME[kind]) {
    const close = document.createElement("button");
    close.className = "btn";
    close.type = "button";
    close.textContent = "閉じる";
    close.style.marginLeft = "auto";
    close.addEventListener("click", () => el.remove());
    el.appendChild(close);
  }

  host.appendChild(el);
  const life = LIFETIME[kind];
  if (life) setTimeout(() => el.remove(), life);
  return el;
}

/**
 * 記録番号を添える(`X-Request-Id`)。押せば設定「記録」でその前後が見える。
 *
 * **断り(4xx)は番号だけ、思わぬエラーは「記録を見る」も出す。** 断りは
 * 理由が文言に書いてあるので、普段は番号を見るだけで足りる。
 */
export function addRecordNo(el, rid, { link = true } = {}) {
  if (!el || !rid || el.querySelector(".toast__rid")) return;
  const text = el.querySelector(".toast__text");
  if (text && !text.textContent.includes(rid)) {
    const small = document.createElement("small");
    small.className = "toast__rid";
    small.textContent = `記録番号 ${rid}`;
    text.appendChild(small);
  }
  if (!link) return;
  const a = document.createElement("a");
  a.className = "btn";
  a.href = `/settings?tab=log&rid=${encodeURIComponent(rid)}`;
  a.textContent = "記録を見る";
  a.style.marginLeft = "auto";
  // 移った先で同じ知らせが残らないように(差し替えでは知らせの場所は残る)
  a.addEventListener("click", () => setTimeout(() => el.remove(), 0));
  const close = el.querySelector("button:last-of-type");
  if (close) close.style.marginLeft = "0";
  el.insertBefore(a, close || null);
}

/**
 * 断り・エラーを知らせる。記録番号を添える。
 *
 * @param {Error|any} err
 * @param {"error"|"warn"} [kind] 断り(4xx)の色。思わぬエラー(5xx・画面の不具合)はいつも "error"
 */
export function toastError(err, kind = "error") {
  const isApi = !!(err && err.name === "ApiError");
  const unexpected = !isApi || !err.status || err.status >= 500;
  const el = toast(err && err.message ? err.message : String(err), unexpected ? "error" : kind);
  if (isApi) {
    // サーバが残した記録の番号
    addRecordNo(el, err.rid, { link: !err.status || err.status >= 500 });
  } else if (err instanceof Error) {
    // 画面の中の不具合(JavaScript)。サーバは知らないので、ここから送る
    report("caught", err).then((rid) => addRecordNo(el, rid));
  }
  return el;
}
