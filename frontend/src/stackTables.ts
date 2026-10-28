/** Таблицы на узких экранах превращаются в карточки (стили — design.css, «table.tbl-cards»): у каждой ячейки подпись из шапки, горизонтальной прокрутки нет. */

/** Можно ли показать таблицу карточками: одна строка шапки без объединённых ячеек и в каждой строке тела (кроме строк-заголовков групп) столько ячеек, сколько столбцов. */
export function canStack(headers: string[], rowCells: number[]): boolean {
  const real = rowCells.filter((n) => n !== 0);                                   // 0 — строка-заголовок группы (одна ячейка на всю ширину): остаётся как есть
  return headers.length >= 2 && real.length > 0 && real.every((n) => n === headers.length);
}

function annotate(t: HTMLTableElement): void {
  if (t.classList.contains("hist-table") || t.classList.contains("no-stack") || t.closest(".gd")) return;      // у истории и у документов Glass своя раскладка карточек
  const hr = t.tHead && t.tHead.rows.length === 1 ? t.tHead.rows[0] : null;
  const heads = hr ? [...hr.cells] : [];
  const headers = heads.map((c) => (c.textContent ?? "").trim());
  const rows = [...t.tBodies].flatMap((b) => [...b.rows]);
  const merged = heads.some((c) => c.colSpan > 1);
  const counts = rows.map((r) => (r.cells.length === 1 && r.cells[0].colSpan > 1 ? 0 : [...r.cells].some((c) => c.colSpan > 1) ? -1 : r.cells.length));
  const ok = !merged && canStack(headers, counts);
  t.classList.toggle("tbl-cards", ok);
  if (!ok) return;
  for (const r of rows) r.classList.toggle("tbl-group", r.cells.length === 1 && r.cells[0].colSpan > 1);
  for (const r of rows) if (!r.classList.contains("tbl-group")) [...r.cells].forEach((c, i) => { if (c.getAttribute("data-label") !== headers[i]) c.setAttribute("data-label", headers[i]); });
}

/** Подписывает ячейки всех таблиц страницы и следит за изменениями (React перерисовывает строки). Возвращает функцию отключения. */
export function watchTables(root: ParentNode = document): () => void {
  let raf = 0;
  const run = () => { raf = 0; root.querySelectorAll("table").forEach(annotate); };
  const schedule = () => { if (!raf) raf = requestAnimationFrame(run); };
  const mo = new MutationObserver(schedule);
  mo.observe(document.body, { childList: true, subtree: true });
  schedule();
  return () => { mo.disconnect(); if (raf) cancelAnimationFrame(raf); };
}
