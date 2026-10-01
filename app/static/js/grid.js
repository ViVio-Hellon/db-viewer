/*
  grid.js — 絞り込み・並べ替え・列の設定ができる表(一覧と閲覧で共通)

  VBA の一覧は ListView の限界に合わせた作りだった(1 ページ数十行、フィルタは
  決めた列だけ、並べ替えは 1 段)。ここでは全行を受け取り、画面の中で:

    - 見出しを押す → 昇順 → 降順 → 元の並び(Shift を押しながらで 2 段目以降)
    - 見出しの ▾ → その列の値の一覧(件数つき)で絞る、数値・日付は範囲でも
    - 上の検索欄 → どの列にあるかを問わず探す(全角/半角・大文字/小文字を区別しない)
    - 列の設定 → 表示する列・並び(ドラッグでも)・幅(見出しの右端をドラッグ)

  絞り込み・並べ替え・選んだ行はタブごとに覚える(sessionStorage)。列の見せ方は
  サーバに保存し、次に開いたときも同じ(`onViewChange`)。
*/

const CHUNK = 300;                       // 一度に描く行数(下まで見たら足す)
const MAX_VALUES = 1000;                 // 値の一覧に並べる上限
const collator = new Intl.Collator("ja", { numeric: true, sensitivity: "base" });
const KIND_LABEL = { num: "数値", date: "日付", text: "文字" };

export const norm = (s) => String(s ?? "").normalize("NFKC").toLowerCase();

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) node.append(kid);
  return node;
}

function debounce(fn, ms) {
  let t = 0;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function toNum(v) {
  const n = parseFloat(String(v).replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

/** 日付を比べられる形(YYYY-MM-DD hh:mm:ss)に。 */
function toDate(v) {
  const m = String(v).trim().match(/^(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
  if (!m) return null;
  const p = (x) => String(x || 0).padStart(2, "0");
  return `${m[1]}-${p(m[2])}-${p(m[3])} ${p(m[4])}:${p(m[5])}:${p(m[6])}`;
}

/** 文字の幅のあたり(13px の字で)。全角は広く、半角カナは狭く。 */
function textWidth(s) {
  let w = 0;
  for (const ch of String(s)) {
    const c = ch.codePointAt(0);
    w += (c >= 0xff61 && c <= 0xff9f) ? 7 : c > 0x2e80 ? 13.5 : 7.6;
  }
  return w;
}

export function createGrid(root, opts = {}) {
  const st = {
    columns: [], byName: {}, rows: [], keyIndex: -1,
    view: { order: [], hidden: [], widths: {} }, auto: {},
    sort: [], filters: {}, search: "", terms: [],
    selectedKey: null, shown: [], limit: CHUNK, hayVersion: 0,
  };

  /* ---------------- 骨組み ---------------- */
  const search = el("input", { class: "input grid__search", type: "search",
    placeholder: "すべての列から探す(スペースで区切ると、全部を含む行)", "aria-label": "すべての列から探す" });
  const chips = el("div", { class: "grid__chips" });
  const count = el("span", { class: "grid__count", "aria-live": "polite" });
  const clearBtn = el("button", { class: "btn btn--find", type: "button", hidden: true, text: "絞り込みを解除" });
  const colBtn = el("button", { class: "btn btn--find", type: "button" }, el("span", { class: "gl", text: "☰" }), "列の設定");
  const colgroup = el("colgroup");
  const headRow = el("tr");
  const tbody = el("tbody");
  const table = el("table", { class: "grid__table" }, colgroup, el("thead", {}, headRow), tbody);
  const empty = el("p", { class: "grid__empty", hidden: true, text: "条件に合う行がありません。" });
  const wrap = el("div", { class: "grid__wrap", tabindex: "0", "aria-label": "一覧の表" }, table, empty);
  root.replaceChildren(
    el("div", { class: "grid" },
      el("div", { class: "grid__bar" }, search, count, clearBtn, colBtn),
      chips, wrap));

  const pop = el("div", { class: "gpop", role: "dialog", hidden: true });
  document.body.appendChild(pop);
  const dlg = el("dialog", { class: "gcols" });
  document.body.appendChild(dlg);

  /* ---------------- 状態の保存(タブごと) ---------------- */
  function remember() {
    if (!opts.stateKey) return;
    try {
      sessionStorage.setItem(opts.stateKey, JSON.stringify({
        sort: st.sort, filters: st.filters, search: st.search,
        selectedKey: st.selectedKey, scroll: wrap.scrollTop,
      }));
    } catch { /* 覚えられないだけ */ }
  }
  function recall() {
    if (!opts.stateKey) return;
    try {
      const s = JSON.parse(sessionStorage.getItem(opts.stateKey) || "null");
      if (!s) return;
      st.sort = Array.isArray(s.sort) ? s.sort : [];
      st.filters = s.filters && typeof s.filters === "object" ? s.filters : {};
      st.search = s.search || "";
      st.selectedKey = s.selectedKey ?? null;
      st.restoreScroll = s.scroll || 0;
    } catch { /* 壊れていれば最初から */ }
  }

  /* ---------------- 列 ---------------- */
  const visible = () => st.view.order.filter((n) => st.byName[n] && !st.view.hidden.includes(n));
  const widthOf = (n) => st.view.widths[n] || st.auto[n] || 120;

  function autoWidths() {
    st.auto = {};
    const sample = st.rows.slice(0, 400);
    for (const c of st.columns) {
      let w = textWidth(c.name) + 34;
      for (const r of sample) w = Math.max(w, textWidth(r.r[c.i] ?? "") + 20);
      st.auto[c.name] = Math.round(Math.min(Math.max(w, 56), 340));
    }
  }

  const saveView = debounce(() => { if (opts.onViewChange) opts.onViewChange(st.view); }, 400);

  /* ---------------- 絞り込み ---------------- */
  function hay(row) {
    if (row.hv !== st.hayVersion) {
      row.h = norm(visible().map((n) => row.r[st.byName[n].i] ?? "").join(""));
      row.hv = st.hayVersion;
    }
    return row.h;
  }

  // 値の絞り込みを Set にしたもの。**条件そのものには持たせない**(sessionStorage に
  // 書くと Set が {} になり、戻したときに壊れる)
  const setCache = new WeakMap();

  function cellOk(col, v, f) {
    if (f.values) {
      let set = setCache.get(f);
      if (!set) { set = new Set(f.values); setCache.set(f, set); }
      if (!set.has(v)) return false;
    }
    if ((f.min ?? "") !== "" || (f.max ?? "") !== "") {
      if (col.kind === "num") {
        const n = toNum(v);
        if (n === null) return false;
        if (f.min !== "" && f.min != null && n < Number(f.min)) return false;
        if (f.max !== "" && f.max != null && n > Number(f.max)) return false;
      } else if (col.kind === "date") {
        const d = toDate(v);
        if (!d) return false;
        if (f.min && d < `${f.min} 00:00:00`) return false;
        if (f.max && d > `${f.max} 23:59:59`) return false;
      }
    }
    return true;
  }

  function passes(row, skip) {
    for (const [name, f] of Object.entries(st.filters)) {
      if (name === skip) continue;
      const col = st.byName[name];
      if (col && !cellOk(col, row.r[col.i] ?? "", f)) return false;
    }
    if (st.terms.length) {
      const h = hay(row);
      for (const t of st.terms) if (!h.includes(t)) return false;
    }
    return true;
  }

  const isActive = (f) => f && (f.values || (f.min ?? "") !== "" || (f.max ?? "") !== "");

  /* ---------------- 並べ替え ---------------- */
  function cmp(col, a, b) {
    const ea = a === "" || a == null, eb = b === "" || b == null;
    if (ea || eb) return ea && eb ? 0 : ea ? 1 : -1;       // 空欄はいつも下
    if (col.kind === "num") return (toNum(a) ?? 0) - (toNum(b) ?? 0);
    if (col.kind === "date") return (toDate(a) || "").localeCompare(toDate(b) || "");
    return collator.compare(a, b);
  }

  function compute() {
    const out = st.rows.filter((r) => passes(r));
    const keys = st.sort.map((s) => ({ col: st.byName[s.name], dir: s.dir })).filter((s) => s.col);
    if (keys.length) {
      out.sort((x, y) => {
        for (const { col, dir } of keys) {
          const a = x.r[col.i] ?? "", b = y.r[col.i] ?? "";
          const empA = a === "", empB = b === "";
          if (empA !== empB) return empA ? 1 : -1;              // 降順でも空欄は下
          const c = cmp(col, a, b);
          if (c) return c * dir;
        }
        return x.o - y.o;
      });
    }
    st.shown = out;
  }

  /* ---------------- 描く ---------------- */
  function renderHead() {
    const cols = visible();
    colgroup.replaceChildren(...cols.map((n) => el("col", { style: `width:${widthOf(n)}px` })));
    table.style.width = `${cols.reduce((s, n) => s + widthOf(n), 0)}px`;
    headRow.replaceChildren(...cols.map((n) => {
      const col = st.byName[n];
      const si = st.sort.findIndex((s) => s.name === n);
      const mark = si < 0 ? "" : (st.sort[si].dir > 0 ? "▲" : "▼") + (st.sort.length > 1 ? si + 1 : "");
      const th = el("th", { class: `gh gh--${col.kind}`, draggable: "true", dataset: { name: n },
        "aria-sort": si < 0 ? "none" : st.sort[si].dir > 0 ? "ascending" : "descending" },
        el("button", { class: "gh__name", type: "button", title: `${n}(${KIND_LABEL[col.kind]})\n押す: 昇順 → 降順 → 元に戻す / Shift+押す: 並べ替えを足す` },
          el("span", { class: "gh__label", text: n }), el("span", { class: "gh__sort", text: mark })),
        el("button", { class: `gh__flt${isActive(st.filters[n]) ? " is-on" : ""}`, type: "button",
          title: "この列で絞り込む", "aria-label": `${n} で絞り込む`, text: "▾" }),
        el("span", { class: "gh__resize", title: "ドラッグで幅を変える(ダブルクリックで自動)" }));
      return th;
    }));
  }

  function rowEl(row, cols) {
    const tr = el("tr", { dataset: { k: row.k } });
    if (String(row.k) === String(st.selectedKey)) tr.setAttribute("aria-selected", "true");
    for (const n of cols) {
      const col = st.byName[n];
      const v = row.r[col.i] ?? "";
      const td = el("td", { class: col.kind === "num" ? "n" : "", text: v });
      if (v.length > 12) td.title = v;
      tr.appendChild(td);
    }
    return tr;
  }

  function renderBody(reset = true) {
    const cols = visible();
    if (reset) { tbody.replaceChildren(); st.rendered = 0; }
    const upto = Math.min(st.limit, st.shown.length);
    const frag = document.createDocumentFragment();
    for (let i = st.rendered; i < upto; i++) frag.appendChild(rowEl(st.shown[i], cols));
    tbody.appendChild(frag);
    st.rendered = upto;
    empty.hidden = st.shown.length > 0;
  }

  function renderChips() {
    const items = [];
    for (const [name, f] of Object.entries(st.filters)) {
      if (!isActive(f)) continue;
      const parts = [];
      if (f.values) {
        const vals = f.values.map((v) => (v === "" ? "(空欄)" : v));
        parts.push(vals.slice(0, 3).join("、") + (vals.length > 3 ? ` ほか${vals.length - 3}` : "") || "(なし)");
      }
      if ((f.min ?? "") !== "" || (f.max ?? "") !== "") parts.push(`${f.min || ""}〜${f.max || ""}`);
      items.push(el("span", { class: "gchip" },
        el("button", { class: "gchip__body", type: "button", title: "押すと条件を変えられます",
          onclick: (e) => openPop(name, e.currentTarget) },
          el("b", { text: name }), `: ${parts.join(" / ")}`),
        el("button", { class: "gchip__x", type: "button", "aria-label": `${name} の絞り込みを解除`, text: "×",
          onclick: () => { delete st.filters[name]; refresh(); } })));
    }
    chips.replaceChildren(...items);
    chips.hidden = items.length === 0;
    clearBtn.hidden = items.length === 0 && !st.search;
  }

  function renderCount() {
    const all = st.rows.length, n = st.shown.length;
    count.textContent = n === all ? `${all.toLocaleString()} 件` : `${all.toLocaleString()} 件中 ${n.toLocaleString()} 件`;
  }

  function refresh({ keepScroll = false } = {}) {
    st.terms = norm(st.search).split(/\s+/).filter(Boolean);
    compute();
    if (!keepScroll) st.limit = CHUNK;
    renderHead();
    renderBody(true);
    renderChips();
    renderCount();
    if (st.selectedKey !== null && !st.shown.some((r) => String(r.k) === String(st.selectedKey))) {
      st.selectedKey = null;
      if (opts.onSelect) opts.onSelect(null);
    }
    remember();
    if (opts.onChange) opts.onChange();
  }

  /* ---------------- 選ぶ・開く ---------------- */
  function rowByKey(k) { return st.rows.find((r) => String(r.k) === String(k)) || null; }

  function select(k, { scroll = false } = {}) {
    st.selectedKey = k;
    for (const tr of tbody.querySelectorAll("tr[aria-selected]")) tr.removeAttribute("aria-selected");
    const idx = st.shown.findIndex((r) => String(r.k) === String(k));
    if (idx >= st.rendered) { st.limit = idx + CHUNK; renderBody(false); }
    const tr = tbody.querySelector(`tr[data-k="${CSS.escape(String(k))}"]`);
    if (tr) { tr.setAttribute("aria-selected", "true"); if (scroll) tr.scrollIntoView({ block: "nearest" }); }
    remember();
    const row = rowByKey(k);
    if (opts.onSelect) opts.onSelect(row ? row.r : null);
  }

  tbody.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-k]");
    if (tr) select(tr.dataset.k);
  });
  tbody.addEventListener("dblclick", (e) => {
    const tr = e.target.closest("tr[data-k]");
    if (!tr) return;
    select(tr.dataset.k);
    const row = rowByKey(tr.dataset.k);
    if (row && opts.onOpen) opts.onOpen(row.r);
  });
  wrap.addEventListener("keydown", (e) => {
    if (!["ArrowDown", "ArrowUp", "Enter", "Home", "End", "PageDown", "PageUp"].includes(e.key)) return;
    if (!st.shown.length) return;
    e.preventDefault();
    let i = st.shown.findIndex((r) => String(r.k) === String(st.selectedKey));
    if (e.key === "Enter") {
      if (i >= 0 && opts.onOpen) opts.onOpen(st.shown[i].r);
      return;
    }
    const page = Math.max(1, Math.floor(wrap.clientHeight / 30) - 1);
    const step = { ArrowDown: 1, ArrowUp: -1, PageDown: page, PageUp: -page }[e.key];
    if (e.key === "Home") i = 0;
    else if (e.key === "End") i = st.shown.length - 1;
    else i = i < 0 ? 0 : Math.min(st.shown.length - 1, Math.max(0, i + step));
    select(st.shown[i].k, { scroll: true });
  });
  wrap.addEventListener("scroll", () => {
    if (st.rendered < st.shown.length && wrap.scrollTop + wrap.clientHeight > wrap.scrollHeight - 600) {
      st.limit += CHUNK;
      renderBody(false);
    }
    rememberSoon();
  });
  const rememberSoon = debounce(remember, 300);

  /* ---------------- 見出し: 並べ替え・絞り込み・幅・並び ---------------- */
  headRow.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-name]");
    if (!th) return;
    const name = th.dataset.name;
    if (e.target.closest(".gh__flt")) { openPop(name, e.target.closest(".gh__flt")); return; }
    if (!e.target.closest(".gh__name")) return;
    const cur = st.sort.find((s) => s.name === name);
    if (e.shiftKey) {
      if (!cur) st.sort.push({ name, dir: 1 });
      else if (cur.dir > 0) cur.dir = -1;
      else st.sort = st.sort.filter((s) => s.name !== name);
    } else {
      st.sort = !cur || st.sort.length > 1 ? [{ name, dir: 1 }] : cur.dir > 0 ? [{ name, dir: -1 }] : [];
    }
    refresh();
  });

  let resizing = null;
  headRow.addEventListener("pointerdown", (e) => {
    const h = e.target.closest(".gh__resize");
    if (!h) return;
    e.preventDefault();
    const th = h.closest("th");
    th.draggable = false;
    resizing = { name: th.dataset.name, x: e.clientX, w: widthOf(th.dataset.name), th };
    h.setPointerCapture(e.pointerId);
  });
  headRow.addEventListener("pointermove", (e) => {
    if (!resizing) return;
    const w = Math.max(40, Math.min(800, Math.round(resizing.w + e.clientX - resizing.x)));
    st.view.widths[resizing.name] = w;
    const i = visible().indexOf(resizing.name);
    if (colgroup.children[i]) colgroup.children[i].style.width = `${w}px`;
    table.style.width = `${visible().reduce((s, n) => s + widthOf(n), 0)}px`;
  });
  headRow.addEventListener("pointerup", () => {
    if (!resizing) return;
    resizing.th.draggable = true;
    resizing = null;
    saveView();
  });
  headRow.addEventListener("dblclick", (e) => {
    const h = e.target.closest(".gh__resize");
    if (!h) return;
    delete st.view.widths[h.closest("th").dataset.name];
    renderHead();
    saveView();
  });

  let dragName = null;
  headRow.addEventListener("dragstart", (e) => {
    const th = e.target.closest("th[data-name]");
    if (!th || resizing) { e.preventDefault(); return; }
    dragName = th.dataset.name;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragName);
  });
  headRow.addEventListener("dragover", (e) => {
    const th = e.target.closest("th[data-name]");
    if (!th || !dragName) return;
    e.preventDefault();
    const r = th.getBoundingClientRect();
    for (const x of headRow.children) x.classList.remove("drop-l", "drop-r");
    th.classList.add(e.clientX < r.left + r.width / 2 ? "drop-l" : "drop-r");
  });
  headRow.addEventListener("dragleave", (e) => {
    const th = e.target.closest("th[data-name]");
    if (th) th.classList.remove("drop-l", "drop-r");
  });
  headRow.addEventListener("drop", (e) => {
    const th = e.target.closest("th[data-name]");
    for (const x of headRow.children) x.classList.remove("drop-l", "drop-r");
    if (!th || !dragName || th.dataset.name === dragName) { dragName = null; return; }
    e.preventDefault();
    const r = th.getBoundingClientRect();
    moveColumn(dragName, th.dataset.name, e.clientX >= r.left + r.width / 2);
    dragName = null;
  });
  headRow.addEventListener("dragend", () => { dragName = null; });

  function moveColumn(name, target, after) {
    const order = st.view.order.filter((n) => n !== name);
    const at = order.indexOf(target);
    order.splice(at + (after ? 1 : 0), 0, name);
    st.view.order = order;
    renderHead(); renderBody(true);
    saveView();
    if (dlg.open) renderCols();
  }

  /* ---------------- 絞り込みの窓 ---------------- */
  let popName = null;
  function closePop() { pop.hidden = true; popName = null; }

  function openPop(name, anchor) {
    const col = st.byName[name];
    if (!col) return;
    popName = name;
    const f = st.filters[name] || {};
    // 値の一覧は「ほかの条件だけで絞った行」から(Excel のオートフィルタと同じ)
    const counts = new Map();
    for (const r of st.rows) {
      if (!passes(r, name)) continue;
      const v = r.r[col.i] ?? "";
      counts.set(v, (counts.get(v) || 0) + 1);
    }
    const values = [...counts.keys()].sort((a, b) => cmp(col, a, b));
    const chosen = new Set(f.values || values);

    const find = el("input", { class: "input", type: "search", placeholder: "値を探す" });
    const list = el("div", { class: "gpop__list" });
    const allBox = el("input", { type: "checkbox" });
    const note = el("p", { class: "why", hidden: true });

    function matching() {
      const q = norm(find.value);
      return q ? values.filter((v) => norm(v === "" ? "(空欄)" : v).includes(q)) : values;
    }
    function apply() {
      const fl = st.filters[name] || {};
      fl.values = chosen.size === values.length ? null : [...chosen];
      setCache.delete(fl);
      if (!isActive(fl)) delete st.filters[name]; else st.filters[name] = fl;
      refresh({ keepScroll: true });
    }
    function drawList() {
      const m = matching();
      const items = m.slice(0, MAX_VALUES).map((v) => {
        const box = el("input", { type: "checkbox" });
        box.checked = chosen.has(v);
        box.addEventListener("change", () => {
          if (box.checked) chosen.add(v); else chosen.delete(v);
          syncAll(); apply();
        });
        return el("label", { class: "gpop__item" }, box,
          el("span", { class: v === "" ? "gpop__v is-blank" : "gpop__v", text: v === "" ? "(空欄)" : v }),
          el("span", { class: "gpop__n", text: counts.get(v).toLocaleString() }));
      });
      list.replaceChildren(...items);
      note.hidden = m.length <= MAX_VALUES;
      note.textContent = `値が ${m.length.toLocaleString()} 種類あります。先頭 ${MAX_VALUES} 件だけ出しています。「値を探す」で絞ってください。`;
      syncAll();
    }
    function syncAll() {
      const m = matching();
      const on = m.filter((v) => chosen.has(v)).length;
      allBox.checked = on === m.length && m.length > 0;
      allBox.indeterminate = on > 0 && on < m.length;
    }
    allBox.addEventListener("change", () => {
      for (const v of matching()) { if (allBox.checked) chosen.add(v); else chosen.delete(v); }
      drawList(); apply();
    });
    find.addEventListener("input", drawList);
    find.addEventListener("keydown", (e) => {
      // Enter: 探した値だけにする(Excel の「検索結果だけ」)
      if (e.key !== "Enter") return;
      e.preventDefault();
      const m = new Set(matching());
      for (const v of values) { if (m.has(v)) chosen.add(v); else chosen.delete(v); }
      drawList(); apply();
    });

    const sortBtn = (dir, label) => el("button", { class: "btn btn--find", type: "button", text: label,
      onclick: () => { st.sort = dir ? [{ name, dir }] : st.sort.filter((s) => s.name !== name); refresh(); } });

    let range = null;
    if (col.kind === "num" || col.kind === "date") {
      const type = col.kind === "num" ? "number" : "date";
      const lo = el("input", { class: "input", type, value: f.min ?? "", "aria-label": col.kind === "num" ? "最小" : "から" });
      const hi = el("input", { class: "input", type, value: f.max ?? "", "aria-label": col.kind === "num" ? "最大" : "まで" });
      const setRange = debounce(() => {
        const fl = st.filters[name] || {};
        fl.min = lo.value; fl.max = hi.value;
        if (!isActive(fl)) delete st.filters[name]; else st.filters[name] = fl;
        refresh({ keepScroll: true });
      }, 250);
      lo.addEventListener("input", setRange); hi.addEventListener("input", setRange);
      range = el("div", { class: "gpop__range" },
        el("span", { class: "why", text: col.kind === "num" ? "範囲" : "期間" }), lo, el("span", { text: "〜" }), hi);
    }

    pop.replaceChildren(
      el("div", { class: "gpop__head" }, el("b", { text: name }),
        el("span", { class: "why", text: KIND_LABEL[col.kind] }),
        el("button", { class: "gpop__x", type: "button", "aria-label": "閉じる", text: "×", onclick: closePop })),
      el("div", { class: "btn-row gpop__sort" }, sortBtn(1, "▲ 昇順"), sortBtn(-1, "▼ 降順"), sortBtn(0, "並べ替えなし")),
      ...(range ? [range] : []),
      find,
      el("label", { class: "gpop__item gpop__all" }, allBox, el("span", { text: "すべて" }),
        el("span", { class: "gpop__n", text: values.length.toLocaleString() + " 種類" })),
      list, note,
      el("div", { class: "btn-row" },
        el("button", { class: "btn btn--find", type: "button", text: "この列の絞り込みを解除",
          onclick: () => { delete st.filters[name]; refresh(); closePop(); } })));
    drawList();
    pop.hidden = false;
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - w - 8))}px`;
    pop.style.top = `${Math.max(8, Math.min(r.bottom + 4, window.innerHeight - h - 8))}px`;
    find.focus();
  }

  document.addEventListener("pointerdown", (e) => {
    if (pop.hidden) return;
    if (pop.contains(e.target) || e.target.closest(".gh__flt") || e.target.closest(".gchip__body")) return;
    closePop();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) closePop(); });
  window.addEventListener("resize", closePop);

  /* ---------------- 列の設定 ---------------- */
  function renderCols() {
    let dragging = null;
    const items = st.view.order.filter((n) => st.byName[n]).map((n) => {
      const box = el("input", { type: "checkbox" });
      box.checked = !st.view.hidden.includes(n);
      box.addEventListener("change", () => {
        st.view.hidden = box.checked ? st.view.hidden.filter((x) => x !== n) : [...st.view.hidden, n];
        if (st.view.hidden.length >= st.columns.length) { st.view.hidden = st.view.hidden.slice(0, -1); box.checked = true; }
        st.hayVersion++;
        refresh({ keepScroll: true }); saveView();
      });
      const move = (d) => {
        const o = st.view.order; const i = o.indexOf(n); const j = i + d;
        if (j < 0 || j >= o.length) return;
        [o[i], o[j]] = [o[j], o[i]];
        renderHead(); renderBody(true); renderCols(); saveView();
      };
      const li = el("li", { class: "gcols__item", draggable: "true", dataset: { name: n } },
        el("span", { class: "gcols__grip", "aria-hidden": "true", text: "⠿" }),
        el("label", {}, box, el("span", { text: n })),
        el("small", { class: "why", text: KIND_LABEL[st.byName[n].kind] }),
        el("button", { class: "btn btn--find gcols__mv", type: "button", "aria-label": `${n} を上へ`, text: "▲", onclick: () => move(-1) }),
        el("button", { class: "btn btn--find gcols__mv", type: "button", "aria-label": `${n} を下へ`, text: "▼", onclick: () => move(1) }));
      li.addEventListener("dragstart", (e) => { dragging = n; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", n); });
      li.addEventListener("dragover", (e) => { if (dragging) { e.preventDefault(); li.classList.add("is-over"); } });
      li.addEventListener("dragleave", () => li.classList.remove("is-over"));
      li.addEventListener("drop", (e) => {
        e.preventDefault(); li.classList.remove("is-over");
        if (dragging && dragging !== n) moveColumn(dragging, n, false);
        dragging = null;
      });
      return li;
    });
    listEl.replaceChildren(...items);
  }
  const listEl = el("ol", { class: "gcols__list" });
  dlg.append(
    el("form", { method: "dialog", class: "browser__head" }, el("b", { text: "列の設定" }),
      el("button", { class: "btn btn--danger", value: "cancel", text: "閉じる" })),
    el("p", { class: "why", text: "チェックを外すと隠れます。▲▼ かドラッグで並びを変えます(表の見出しをドラッグしても変わります)。幅は表の見出しの右端をドラッグ。変えるとすぐ保存され、次に開いたときも同じです。" }),
    el("div", { class: "btn-row" },
      el("button", { class: "btn btn--find", type: "button", text: "すべて表示",
        onclick: () => { st.view.hidden = []; st.hayVersion++; refresh({ keepScroll: true }); renderCols(); saveView(); } }),
      el("button", { class: "btn btn--find", type: "button", text: "幅を自動に戻す",
        onclick: () => { st.view.widths = {}; renderHead(); saveView(); } }),
      el("button", { class: "btn btn--find", type: "button", text: "初期の並びに戻す",
        onclick: async () => {
          if (opts.onViewReset) { const v = await opts.onViewReset(); if (v) st.view = v; }
          else st.view = { order: st.columns.map((c) => c.name), hidden: [], widths: {} };
          st.hayVersion++; refresh({ keepScroll: true }); renderCols();
        } })),
    listEl);
  colBtn.addEventListener("click", () => { renderCols(); dlg.showModal(); });

  /* ---------------- 検索欄 ---------------- */
  const onSearch = debounce(() => { st.search = search.value; refresh(); }, 180);
  search.addEventListener("input", onSearch);
  clearBtn.addEventListener("click", () => { st.filters = {}; st.search = ""; search.value = ""; refresh(); });

  recall();
  search.value = st.search;

  /* ---------------- 外から使うもの ---------------- */
  return {
    setData({ columns, rows, keyIndex = -1, view }) {
      st.columns = columns.map((c, i) => ({ ...c, i }));
      st.byName = Object.fromEntries(st.columns.map((c) => [c.name, c]));
      st.keyIndex = keyIndex;
      st.rows = rows.map((r, o) => ({ r, o, k: keyIndex >= 0 ? String(r[keyIndex] ?? "") : String(o) }));
      st.view = view ? { order: [...view.order], hidden: [...view.hidden], widths: { ...view.widths } }
        : { order: st.columns.map((c) => c.name), hidden: [], widths: {} };
      // もう無い列の条件は捨てる
      st.sort = st.sort.filter((s) => st.byName[s.name]);
      for (const n of Object.keys(st.filters)) if (!st.byName[n]) delete st.filters[n];
      st.hayVersion++;
      autoWidths();
      const scroll = st.restoreScroll; st.restoreScroll = 0;
      refresh();
      if (scroll) {
        while (st.rendered < st.shown.length && wrap.scrollHeight < scroll + wrap.clientHeight) { st.limit += CHUNK; renderBody(false); }
        wrap.scrollTop = scroll;
      }
      if (st.selectedKey !== null) select(st.selectedKey);
    },
    shownRows: () => st.shown.map((r) => r.r),
    shownKeys: () => st.shown.map((r) => r.k),
    visibleColumns: () => visible(),
    columns: () => st.columns.map((c) => c.name),
    view: () => st.view,
    selected: () => (st.selectedKey === null ? null : (rowByKey(st.selectedKey)?.r || null)),
    selectedKey: () => st.selectedKey,
    total: () => st.rows.length,
    focus: () => wrap.focus(),
  };
}
