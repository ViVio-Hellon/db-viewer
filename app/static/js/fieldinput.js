/*
  fieldinput.js — 列の「入力」の種類どおりの入力欄(一覧のマス・行の入力窓で同じもの)

    text     … ふつうの欄
    combo    … コンボボックス(その列に入っている値から選ぶ。打ってもよい)<input list> + <datalist>
    spin     … スピンボタン(数)。<input type=number step=刻み>
    calendar … カレンダー(日付)。<input type=date>。書くときの形(2026/10/15)はサーバがそろえる
    auto_new_user / auto_upd_user … 自動(登録した人・直した人)。直せない。書くときにサーバが入れる
    file     … ファイルの場所(文字)+「プレビュー」(アプリの窓の中で見る。ほかのアプリでは開かない)

  makeField(spec, value) は <span>(入力欄 + 選択肢)を返す。値は inputOf(span).value。
*/
import { tokenUrl } from "./api.js";
import { openWindow } from "./desktop.js";
import { toast } from "./toast.js";

let seq = 0;

/** ファイルの列の値を、アプリの窓でプレビューする(その表のその列に入っている値だけ)。 */
export function previewFile(table, column, value) {
  const v = String(value ?? "").trim();
  if (!v) { toast(`${column}: ファイルの場所が入っていません。`, "info"); return; }
  const q = new URLSearchParams({ table, column, value: v });
  if (!openWindow(tokenUrl(`/api/file/preview?${q}`), `${column} — ${v}`)) {
    toast("別の窓を開けませんでした。ポップアップの許可を確認してください。", "warn");
  }
}

/** "2026/10/15" "2026-10-15" "20261015" → "2026-10-15"(読めなければ "")。 */
export function toIsoDate(text) {
  const t = String(text ?? "").trim();
  const m = t.match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/) || t.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (!m) return "";
  return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
}

export function makeField(spec, value, { className = "input", onPreview = null } = {}) {
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
  } else if (kind === "auto_new_user" || kind === "auto_upd_user") {
    inp.value = v;
    inp.readOnly = true;
    inp.classList.add("is-auto");
    inp.placeholder = "自動で入ります";
    inp.title = kind === "auto_new_user" ? "足したときに、ログインした人が入ります" : "足した・直したときに、ログインした人が入ります";
  } else if (kind === "file") {
    inp.value = v;
    inp.placeholder = "例: 手順書\\0-1-1.pdf(参照ファイルの起点から)";
    if (onPreview) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn btn--find fld__btn"; b.textContent = "プレビュー";
      b.title = "保存してある値のファイルを、アプリの窓で見ます";
      b.addEventListener("click", () => onPreview(inp.value));
      wrap.appendChild(b);
    }
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
export const INPUT_LABEL = { text: "文字", combo: "コンボボックス", spin: "スピンボタン", calendar: "カレンダー",
                             auto_new_user: "自動: 登録した人", auto_upd_user: "自動: 直した人", file: "ファイル" };
