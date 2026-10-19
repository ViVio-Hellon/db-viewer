/* この PC の権限の画面(0.16.0)。パスワードは無い: ログイン ID・PC 名で、梱包資材マスタの「アクセス権限」を引く。 */
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";

export function start(nextUrl) {
  const form = document.getElementById("loginForm");
  const problem = document.getElementById("loginProblem");
  const go = document.getElementById("loginExe");
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
