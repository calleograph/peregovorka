// Служебное сочетание Alt+M открывает встроенную мини-игру. Обрабатывается только событие страницы (глобального перехвата клавиатуры ОС нет);
// код игры подгружается отдельным файлом при первом нажатии и на остальную работу приложения не влияет.
export function isCrystalsKey(e: Pick<KeyboardEvent, "code" | "key" | "altKey" | "ctrlKey" | "shiftKey" | "metaKey" | "isComposing">): boolean {
  if (!e.altKey || e.ctrlKey || e.shiftKey || e.metaKey || e.isComposing) return false;
  // Физический код — работает в любой раскладке; если кода нет (виртуальная клавиатура, удалённый рабочий стол) — латинская «m» или русская «ь».
  return e.code === "KeyM" || (!e.code && /^[mь]$/i.test(e.key));
}

let loading: Promise<typeof import("./crystals/game.js")> | null = null;

export function openCrystals(): void {
  loading ??= import("./crystals/game.js");
  loading.then((m) => m.open()).catch(() => { loading = null; });       // сбой загрузки не должен ничего ломать на странице
}

export function installCrystalsHotkey(): () => void {
  const onKey = (e: KeyboardEvent) => {
    if (!isCrystalsKey(e)) return;
    e.preventDefault(); e.stopPropagation();
    if (!e.repeat) openCrystals();
  };
  document.addEventListener("keydown", onKey, true);
  return () => document.removeEventListener("keydown", onKey, true);
}
