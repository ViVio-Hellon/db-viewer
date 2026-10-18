/* この PC の権限の画面(0.16.0)。パスワードは無い: ログイン ID・PC 名で、梱包資材マスタの「アクセス権限」を引く。 */
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";

export function start(nextUrl) {
  const form = document.getElementById("loginForm");
  const problem = document.getElementById("loginProblem");
  const go = document.getElementById("loginExe");
  // VBA regUF: オンライン/オフラインを選んで保存 → 「設定しました。」
  const modeSet = document.getElementById("modeSet");
  if (modeSet) modeSet.addEventListener("click", async () => {
    const picked = document.querySelector("input[name=bootMode]:checked");
    try {
      await api.post("/api/settings/save", { online: !picked || picked.value === "online" });
      toast("設定しました。", "ok");
      document.getElementById("modeCard").hidden = true;
      document.getElementById("loginCard").hidden = false;
      go.focus();
    } catch (err) { toastError(err); }
  });
  // VBA Login.LOCKBT_DblClick: 起動モード設定情報の初期化
  const resetMode = document.getElementById("resetMode");
  if (resetMode) resetMode.addEventListener("click", async () => {
    if (!(await confirmBox(resetMode.dataset.ask || "起動モード設定情報を初期化しますか？",
                           { ok: "初期化する", danger: true }))) return;
    try {
      const res = await api.post("/api/settings/reset-mode", {});
      toast(res.message, "ok");
      document.getElementById("loginCard").hidden = true;
      document.getElementById("modeCard").hidden = false;
    } catch (err) { toastError(err); }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.hidden = true;
    try {
      const body = await api.post("/api/login", { next: nextUrl });
      toast(`${body.label}: ${body.note || ""}`, body.edit ? "ok" : "info");
      location.href = body.next || "/list";
    } catch (err) {
      problem.hidden = false;
      problem.textContent = err.message;
      if (err.status !== 403) toastError(err);
    }
  });
  go.focus();
}
