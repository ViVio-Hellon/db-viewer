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
