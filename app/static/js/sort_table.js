/*
  sort_table.js — ふつうの <table> を「列名を押すと並べ替え」にする

  マスタ管理の表と、マスタから選ぶ窓(「…」)で使う。決まりは一覧(grid.js)と同じ:
    - 列名を押す: 昇順 → 降順 → 元の並び
    - 数値・日付の列は値として、文字は日本語の並び(0-1-2 < 0-1-10)
    - 空欄は昇順でも降順でもいちばん下
  行の要素をそのまま並べ替えるので、選んでいる行(aria-selected)はずれない。
  `stateKey` を渡すと並べ替えをタブの中で覚える(表を描き直しても同じ並び)。
*/
import { compareValues, guessKind } from "./grid.js";

export function makeSortable(table, { stateKey = "" } = {}) {
  let sort = null;                                   // { name, dir: 1 | -1 }
  if (stateKey) {
    try { sort = JSON.parse(sessionStorage.getItem(stateKey) || "null"); } catch { sort = null; }
  }

  const headCells = () => [...(table.tHead?.rows[0]?.cells || [])];
  const nameOf = (th) => th.dataset.name || th.textContent.replace(/[▲▼]$/, "").trim();

  function remember() {
    if (!stateKey) return;
    try { sessionStorage.setItem(stateKey, JSON.stringify(sort)); } catch { /* 覚えられないだけ */ }
  }

  /** 見出しを押せる形にする(描き直したら呼び直す)。 */
  function decorate() {
    for (const th of headCells()) {
      if (th.dataset.sortable === "1") continue;
      const name = th.textContent.trim();
      th.dataset.name = name;
      th.dataset.sortable = "1";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ths";
      btn.title = `${name}\n押す: 昇順 → 降順 → 元の並び`;
      const label = document.createElement("span");
      label.textContent = name;
      const mark = document.createElement("span");
      mark.className = "ths__mark";
      btn.append(label, mark);
      th.replaceChildren(btn);
      btn.addEventListener("click", () => {
        if (!sort || sort.name !== name) sort = { name, dir: 1 };
        else if (sort.dir > 0) sort.dir = -1;
        else sort = null;
        remember();
        apply();
      });
    }
  }

  /** いまの並べ替えを当てる。表の行を描き直したあとにも呼ぶ。 */
  function apply() {
    decorate();
    const body = table.tBodies[0];
    if (!body) return;
    const rows = [...body.rows];
    rows.forEach((tr, i) => { if (tr.dataset.o === undefined) tr.dataset.o = String(i); });
    const ths = headCells();
    const j = sort ? ths.findIndex((th) => nameOf(th) === sort.name) : -1;
    if (sort && j < 0) sort = null;                  // その列がもう無い
    for (const [k, th] of ths.entries()) {
      const on = k === j;
      th.setAttribute("aria-sort", on ? (sort.dir > 0 ? "ascending" : "descending") : "none");
      const mark = th.querySelector(".ths__mark");
      if (mark) mark.textContent = on ? (sort.dir > 0 ? "▲" : "▼") : "";
    }
    let ordered;
    if (j < 0) {
      ordered = rows.sort((a, b) => Number(a.dataset.o) - Number(b.dataset.o));
    } else {
      const text = (tr) => tr.cells[j]?.textContent ?? "";
      const kind = guessKind(rows.map(text));
      ordered = rows.sort((a, b) => {
        const x = text(a), y = text(b);
        if ((x === "") !== (y === "")) return x === "" ? 1 : -1;   // 空欄はいつも下
        return compareValues(kind, x, y) * sort.dir || Number(a.dataset.o) - Number(b.dataset.o);
      });
    }
    body.append(...ordered);
  }

  apply();
  return { apply, reset() { sort = null; remember(); apply(); } };
}
