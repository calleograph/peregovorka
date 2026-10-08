/** Высота многострочного поля ввода: растёт вместе с текстом до предела, дальше — прокрутка внутри поля. */
export function growHeight(scrollHeight: number, min: number, max: number): { height: number; scroll: boolean } {
  const h = Math.max(min, Math.min(scrollHeight, max));
  return { height: h, scroll: scrollHeight > max };
}

/** Применить к textarea: сбросить высоту, измерить содержимое и выставить итоговую. */
export function autoGrow(el: HTMLTextAreaElement | null, min = 40, max = 140): void {
  if (!el) return;
  el.style.height = "auto";
  const g = growHeight(el.scrollHeight, min, max);
  el.style.height = `${g.height}px`;
  el.style.overflowY = g.scroll ? "auto" : "hidden";
}
