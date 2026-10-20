/*
  askbox.js — 確かめの窓(はい / やめる)と、名前を打ってもらう窓。

  【なぜブラウザの confirm / prompt を使わないのか】
  デスクトップ版(Tauri の窓)では `window.confirm` / `window.prompt` の窓が**出ないまま**
  答えた扱いで処理が進んだ(python-web-tools で起きた)。行を消す・上書きする・表を入れ替える・
  どれも**取り返しのつかない操作の前の確かめ**で、そこが黙って
  素通りになるのがいちばん危ない。python-web-tools の askbox.js を写したもの。

  画面の中に自分で窓を出すので、ブラウザ版でもデスクトップ版でも同じに動く。
  答えは Promise で返す(`await confirmBox(...)`)。Esc・「やめる」・窓の外を押す は「やめる」。

  使い方:
      if (!(await confirmBox("消します。よろしいですか？", { ok: "消す", danger: true }))) return;
      const name = await promptBox("名前を付けてください");   // やめたら null
*/

let box = null;

function ensure() {
  if (box && document.body.contains(box.dialog)) return box;
  const style = document.createElement("style");
  style.textContent = `
    .askbox { border: 1px solid var(--rule); border-radius: var(--radius, 10px); padding: 0;
              background: var(--surface); color: var(--ink); width: min(520px, 92vw);
              box-shadow: var(--shadow-pop, 0 10px 30px rgb(0 0 0 / 30%)); }
    .askbox::backdrop { background: rgb(0 0 0 / 45%); }
    .askbox__body { padding: 18px 20px 8px; }
    .askbox__text { margin: 0; white-space: pre-wrap; line-height: 1.7;
                    font-size: var(--fs-md, 13px); }
    .askbox__input { margin-top: 12px; width: 100%; }
    .askbox__foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 20px 16px; }
  `;
  const dialog = document.createElement("dialog");
  dialog.className = "askbox";
  dialog.setAttribute("aria-labelledby", "askboxText");
  dialog.innerHTML = `
    <div class="askbox__body">
      <p class="askbox__text" id="askboxText"></p>
      <input class="input askbox__input" id="askboxInput" spellcheck="false" autocomplete="off" hidden>
    </div>
    <div class="askbox__foot">
      <button class="btn" type="button" data-answer="no">やめる</button>
      <button class="btn btn--commit" type="button" data-answer="yes">OK</button>
    </div>`;
  document.head.appendChild(style);
  document.body.appendChild(dialog);
  box = {
    dialog,
    text: dialog.querySelector(".askbox__text"),
    input: dialog.querySelector(".askbox__input"),
    yes: dialog.querySelector('[data-answer="yes"]'),
    no: dialog.querySelector('[data-answer="no"]'),
  };
  return box;
}

function open(message, { ok = "OK", cancel = "やめる", danger = false, input = null } = {}) {
  const b = ensure();
  // 前の窓が開いたまま次を訊かれたら、前のものは「やめる」で閉じる
  if (b.dialog.open) b.dialog.close("no");
  b.text.textContent = message;
  b.yes.textContent = ok;
  b.no.textContent = cancel;
  b.yes.className = `btn ${danger ? "btn--danger" : "btn--commit"}`;
  b.input.hidden = input === null;
  b.input.value = input ?? "";
  return new Promise((resolve) => {
    const finish = (answer) => {
      b.yes.removeEventListener("click", onYes);
      b.no.removeEventListener("click", onNo);
      b.dialog.removeEventListener("close", onClose);
      b.dialog.removeEventListener("click", onBackdrop);
      b.input.removeEventListener("keydown", onEnter);
      if (b.dialog.open) b.dialog.close();
      resolve(answer);
    };
    const onYes = () => finish(true);
    const onNo = () => finish(false);
    const onClose = () => finish(false);                 // Esc
    const onBackdrop = (event) => { if (event.target === b.dialog) finish(false); };
    const onEnter = (event) => {
      if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); finish(true); }
    };
    b.yes.addEventListener("click", onYes);
    b.no.addEventListener("click", onNo);
    b.dialog.addEventListener("close", onClose);
    b.dialog.addEventListener("click", onBackdrop);
    b.input.addEventListener("keydown", onEnter);
    b.dialog.showModal();
    // 取り返しのつかない操作は「やめる」に最初の焦点を置く(Enter の押し間違いで進めない)
    if (input !== null) b.input.focus();
    else (danger ? b.no : b.yes).focus();
  });
}

/** はい / やめる。はいなら true。 */
export function confirmBox(message, options = {}) {
  return open(message, { ...options, input: null });
}

/** 1行を打ってもらう。やめたら null(空のまま OK なら ""。ブラウザの prompt と同じ)。 */
export async function promptBox(message, { value = "", ...options } = {}) {
  const b = ensure();
  const ok = await open(message, { ...options, input: String(value) });
  return ok ? b.input.value : null;
}

/**
 * いくつかの中から 1 つ選んでもらう(0.24.0。同じ名前のファイルがいくつものフォルダにあるとき)。
 * items = [{ label, sub }]。選んだ番号、やめたら null。
 */
export function chooseBox(message, items) {
  ensure();                                            // 見た目(askbox の style)をそろえる
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "askbox";
    const body = document.createElement("div");
    body.className = "askbox__body";
    const text = document.createElement("p");
    text.className = "askbox__text";
    text.textContent = message;
    const list = document.createElement("div");
    list.style.cssText = "display:flex;flex-direction:column;gap:6px;margin-top:12px;max-height:50vh;overflow:auto";
    items.forEach((item, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn--find";
      b.style.cssText = "justify-content:flex-start;text-align:left;flex-direction:column;align-items:flex-start;height:auto;padding:6px 10px";
      const strong = document.createElement("b");
      strong.textContent = item.label;
      const small = document.createElement("small");
      small.textContent = item.sub || "";
      small.style.color = "var(--muted)";
      b.append(strong, small);
      b.addEventListener("click", () => finish(i));
      list.appendChild(b);
    });
    body.append(text, list);
    const foot = document.createElement("div");
    foot.className = "askbox__foot";
    const no = document.createElement("button");
    no.type = "button"; no.className = "btn"; no.textContent = "やめる";
    no.addEventListener("click", () => finish(null));
    foot.appendChild(no);
    dialog.append(body, foot);
    let done = false;
    function finish(answer) {
      if (done) return;
      done = true;
      if (dialog.open) dialog.close();
      dialog.remove();
      resolve(answer);
    }
    dialog.addEventListener("close", () => finish(null));
    dialog.addEventListener("click", (e) => { if (e.target === dialog) finish(null); });
    document.body.appendChild(dialog);
    dialog.showModal();
    list.querySelector("button")?.focus();
  });
}
