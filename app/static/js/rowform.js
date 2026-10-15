/*
  rowform.js — 行を足す・直す・見る窓(一覧で、VBA 版でない表のとき)

    openRowForm({ title, fields, readOnly }) → Promise<{values} | null>
      fields … [{name, value, input, step, choices}](/api/list/form)
      返す values は {列名: 入力した値}(変えた列だけでなく全部。比べるのはサーバ)
*/
import { makeField, inputOf } from "./fieldinput.js";

let dlg = null;

function build() {
  dlg = document.createElement("dialog");
  dlg.className = "browser rowdlg";
  dlg.setAttribute("aria-labelledby", "rfTitle");
  dlg.innerHTML = `
    <form method="dialog" class="browser__head">
      <b id="rfTitle"></b>
      <button class="btn btn--danger" value="cancel">閉じる</button>
    </form>
    <div class="rowform" id="rfFields"></div>
    <p class="status status--ng" id="rfProblem" hidden></p>
    <div class="btn-row" style="margin-top:8px">
      <button class="btn btn--commit" type="button" id="rfSave">保存する</button>
    </div>`;
  document.body.appendChild(dlg);
}

export function showProblem(text) {
  if (!dlg) return;
  const p = dlg.querySelector("#rfProblem");
  p.hidden = !text; p.textContent = text || "";
}

/**
 * `onSave(values)` が true を返したら閉じる(断られたら窓に理由を出して開いたまま)。
 */
export function openRowForm({ title, fields, readOnly = false, onSave }) {
  if (!dlg) build();
  dlg.querySelector("#rfTitle").textContent = title;
  const box = dlg.querySelector("#rfFields");
  const inputs = [];
  box.replaceChildren(...fields.map((f) => {
    const lab = document.createElement("label");
    lab.className = "rowform__row";
    const name = document.createElement("span"); name.textContent = f.name;
    const field = makeField(f, f.value);
    const inp = inputOf(field);
    inp.readOnly = readOnly;
    if (readOnly && inp.type !== "text") inp.disabled = true;
    inputs.push([f.name, inp, f.value ?? ""]);
    inp.addEventListener("input", () => inp.classList.toggle("is-changed", true));
    lab.append(name, field);
    return lab;
  }));
  showProblem("");
  const save = dlg.querySelector("#rfSave");
  save.hidden = readOnly;
  save.onclick = async () => {
    const values = Object.fromEntries(inputs.map(([n, inp]) => [n, inp.value]));
    save.disabled = true;
    try {
      if (await onSave(values)) dlg.close();
    } finally { save.disabled = false; }
  };
  dlg.showModal();
  const first = inputs.find(([, inp]) => !inp.readOnly);
  if (first) first[1].focus();
}
