/*
  fieldinput.js — 列の「入力」の種類どおりの入力欄(一覧のマス・行の入力窓・マスタ管理で同じもの)

    text     … ふつうの欄
    combo    … コンボボックス(その列に入っている値から選ぶ。打ってもよい)<input list> + <datalist>
    spin     … スピンボタン(数)。<input type=number step=刻み>
    calendar … カレンダー(日付)。<input type=date>。書くときの形(2026/10/15)はサーバがそろえる

  makeField(spec, value) は <span>(入力欄 + 選択肢)を返す。値は inputOf(span).value。
*/
let seq = 0;

/** "2026/10/15" "2026-10-15" "20261015" → "2026-10-15"(読めなければ "")。 */
export function toIsoDate(text) {
  const t = String(text ?? "").trim();
  const m = t.match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/) || t.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (!m) return "";
  return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
}

export function makeField(spec, value, { className = "input" } = {}) {
  const wrap = document.createElement("span");
  wrap.className = "fld";
  const inp = document.createElement("input");
  inp.className = className;
  const v = value ?? "";
  const kind = (spec && spec.input) || "text";
  if (kind === "spin") {
    inp.type = "number";
    inp.step = spec.step || "1";
    inp.inputMode = "decimal";
    inp.value = String(v).replace(/,/g, "");
  } else if (kind === "calendar") {
    const iso = toIsoDate(v);
    if (iso || v === "") { inp.type = "date"; inp.value = iso; }
    else { inp.value = v; inp.placeholder = "2026/10/15"; }            // 日付として読めない値は文字のまま見せる
  } else {
    inp.value = v;
    if (kind === "combo") {
      const dl = document.createElement("datalist");
      dl.id = `fldList${++seq}`;
      for (const c of spec.choices || []) {
        const o = document.createElement("option"); o.value = c; dl.appendChild(o);
      }
      inp.setAttribute("list", dl.id);
      inp.autocomplete = "off";
      inp.title = "▾ で入っている値から選べます(打ってもよい)";
      wrap.appendChild(dl);
    }
  }
  wrap.prepend(inp);
  return wrap;
}

export function inputOf(field) {
  return field.querySelector("input");
}

/** 入力の種類の呼び名(列の設定と同じ)。 */
export const INPUT_LABEL = { text: "文字", combo: "コンボボックス", spin: "スピンボタン", calendar: "カレンダー" };
