/*
  app.js — 全画面で共通の起動処理

  画面ごとの中身は views/ 以下。ここは外枠の面倒だけを見る。
*/

import { api } from "./api.js";
import { startHeartbeat } from "./health.js";
import { toast, toastError, addRecordNo } from "./toast.js";
import * as errlog from "./errlog.js";
import * as busy from "./busy.js";
import * as nav from "./nav.js";
import * as running from "./running.js";
import * as unsaved from "./unsaved.js";

// **押されたボタンを覚えておく。** `api.js` が送信を始めるときに、
// そのボタンを待機の姿にする(無言で待たせない)
busy.watchClicks();

// 拾われなかった画面のエラーを記録へ送り、番号を知らせる
errlog.install((rid) => {
  const el = toast("画面の中で思わぬエラーが起きました。続けて困るときは、記録番号を管理者に伝えてください。", "error");
  addRecordNo(el, rid);
});

startHeartbeat();
// 長い処理は帯に出す。**画面を離れても見える**ようにするため
running.start();

/**
 * リボンの中の道具を繋ぐ。
 *
 * **リボンは画面ごとに作り直される**(値が画面で違う)ので、
 * 差し替えのたびに呼ばれる。同じ要素へ二重に付かないよう、
 * 付けた要素に印を残す ── 押すたびに2回終了しようとしないため。
 */
export function wireShell() {
  errlog.resetCount();
  wireQuit(document.getElementById("quit"));
  wireLogout(document.getElementById("logout"));
  // 新しい帯は空で来る。**移った先で「何も動いていない」に見せない**
  running.paint();
}

function once(node) {
  if (!node || node.dataset.wired === "1") return false;
  node.dataset.wired = "1";
  return true;
}

// 「終了」ボタン (基盤仕様書 2.8)。
// 実行中の処理があるときサーバは 409 を返す。勝手に中断しない
function wireQuit(button) {
  if (!once(button)) return;
  button.addEventListener("click", async () => {
    try {
      const body = await api.post("/api/shutdown", {});
      toast(body.message || "終了します", "ok");
      setTimeout(() => {
        document.body.innerHTML =
          '<main style="padding:48px;font-family:var(--sans)">' +
          "<h1>終了しました</h1><p>このタブは閉じてかまいません。</p></main>";
      }, 700);
    } catch (err) {
      if (err.status === 409) {
        // 中断してよいか・保存せずに終えてよいかは利用者が決める。
        // **聞く文はサーバが持つ**(実行中の処理か、保存していない図か)
        if (confirm(err.message || "終了しますか?")) {
          // 捨ててよいと答えたので、閉じるときにもう一度は聞かない
          unsaved.allowLeave();
          try {
            await api.post("/api/shutdown", { force: true });
            toast("終了します", "ok");
          } catch (e) { toastError(e); }
        }
        return;
      }
      toastError(err);
    }
  });
}

// ログアウト。セッションはプロセスに1つ(VBA と同じく1台1人)
function wireLogout(button) {
  if (!once(button)) return;
  button.addEventListener("click", async () => {
    try {
      const body = await api.post("/api/logout", {});
      unsaved.allowLeave();
      location.href = body.next || "/login";
    } catch (err) { toastError(err); }
  });
}

// 画面切替のキーボード操作。現場は片手作業が多い
document.addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  const match = /^F([1-4])$/.exec(event.key);
  if (!match) return;
  const link = document.querySelectorAll(".rail a")[Number(match[1]) - 1];
  if (link) { event.preventDefault(); link.click(); }
});

wireShell();
nav.start({ onSwap: wireShell });

export { api, toast, toastError };
