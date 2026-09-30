/* ログイン画面。判定はサーバ(VBA と同じ文言)。 */
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";

export function start(nextUrl) {
  const form = document.getElementById("loginForm");
  const id = document.getElementById("loginId");
  const pass = document.getElementById("loginPass");
  const problem = document.getElementById("loginProblem");
  document.getElementById("useMyId").addEventListener("click", () => { id.value = document.getElementById("useMyId").textContent; });
  document.getElementById("showPass").addEventListener("click", () => { pass.type = pass.type === "password" ? "text" : "password"; });
  // VBA regUF: オンライン/オフラインを選んで保存 → 「設定しました。」
  const modeSet = document.getElementById("modeSet");
  if (modeSet) modeSet.addEventListener("click", async () => {
    const picked = document.querySelector("input[name=bootMode]:checked");
    try {
      await api.post("/api/settings/save", { online: !picked || picked.value === "online" });
      toast("設定しました。", "ok");
      document.getElementById("modeCard").hidden = true;
      document.getElementById("loginCard").hidden = false;
      pass.focus();
    } catch (err) { toastError(err); }
  });
  // VBA Login.LOCKBT_DblClick: 起動モード設定情報の初期化
  const resetMode = document.getElementById("resetMode");
  if (resetMode) resetMode.addEventListener("click", async () => {
    if (!confirm(resetMode.dataset.ask || "起動モード設定情報を初期化しますか？")) return;
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
      const body = await api.post("/api/login", { id: id.value, password: pass.value, next: nextUrl });
      if (body.note) toast(body.note, "warn");
      location.href = body.next || "/list";
    } catch (err) {
      problem.hidden = false;
      problem.textContent = err.message;
      if (err.status !== 403) toastError(err);
    }
  });
  pass.focus();
}
