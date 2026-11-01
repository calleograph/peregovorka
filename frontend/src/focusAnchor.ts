/** Переход к полю или блоку раздела (из «Обзора», по адресу ?focus=…): прокрутка к нему, короткая подсветка и фокус на первом поле внутри.
 *  Раздел может догружать данные — элемент ждём до timeoutMs. */
export function focusAnchor(id: string, timeoutMs = 4000): void {
  const started = Date.now();
  const tick = () => {
    const el = document.getElementById(id);
    if (!el) { if (Date.now() - started < timeoutMs) window.setTimeout(tick, 150); return; }
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
    el.classList.add("flash-target");
    window.setTimeout(() => el.classList.remove("flash-target"), 2400);
    el.querySelector<HTMLElement>("input:not([type=hidden]), select, textarea")?.focus({ preventScroll: true });
  };
  tick();
}
