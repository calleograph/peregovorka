// Платформенный слой приложения Peregovorka для Windows (desktop/). В браузере `desktop` = null и всё ниже ничего не делает.
// Мост даёт странице только три действия: уведомление Windows, состояние (непрочитанные, встреча, показ экрана, тема) и приём команд приложения.

export type DesktopCommand = { type: "navigate" | "chats" | "leave-meeting" | "stop-share" | string; route?: string };
type DesktopState = { unread?: number; inCall?: boolean; sharing?: boolean; mic?: boolean; theme?: "light" | "dark"; ws?: string };
type Bridge = {
  api: number; version: string; platform: string;
  notify(n: { tag: string; title: string; body?: string; route?: string; kind?: "chat" | "mention" | "meeting" | "info" }): void;
  setState(s: DesktopState): void;
  onCommand(cb: (c: DesktopCommand) => void): void;
};

const raw = typeof window !== "undefined" ? (window as unknown as { peregovorkaDesktop?: Bridge }).peregovorkaDesktop : undefined;
export const desktop: Bridge | null = raw && raw.api === 1 ? raw : null;

const handlers = new Map<string, Set<(c: DesktopCommand) => void>>();
let listening = false;

/** Подписка на команду приложения (переход по уведомлению, «Чаты» из трея, «Покинуть встречу» с индикатора). Возвращает отписку. */
export function onDesktopCommand(type: string, fn: (c: DesktopCommand) => void): () => void {
  if (!desktop) return () => undefined;
  if (!listening) {
    listening = true;
    desktop.onCommand((c) => handlers.get(c.type)?.forEach((f) => f(c)));
  }
  const set = handlers.get(type) ?? new Set();
  set.add(fn);
  handlers.set(type, set);
  return () => { set.delete(fn); };
}

let last: DesktopState = {};
/** Сообщить приложению изменившееся состояние (повторы не отправляются). */
export function desktopState(p: DesktopState): void {
  if (!desktop) return;
  const changed = (Object.keys(p) as (keyof DesktopState)[]).some((k) => p[k] !== last[k]);
  if (!changed) return;
  last = { ...last, ...p };
  desktop.setState(p);
}

/** Уведомление Windows — когда страницу сейчас не видно (окно свёрнуто, в трее или открыта другая организация). Повтор того же `tag` приложение не покажет. */
export function desktopNotify(n: Parameters<Bridge["notify"]>[0]): boolean {
  if (!desktop || (document.visibilityState === "visible" && document.hasFocus())) return false;
  desktop.notify(n);
  return true;
}

/** Ссылка «открыть в приложении» для текущей страницы встречи; предлагается только в браузере на Windows. */
export const appLink = (url = window.location.href) => `peregovorka://open?url=${encodeURIComponent(url)}`;
export const offerApp = (ua = typeof navigator !== "undefined" ? navigator.userAgent : "") => !desktop && /Windows NT 1\d/.test(ua) && !/Mobi|Android/.test(ua);

if (desktop && typeof document !== "undefined") {
  document.documentElement.classList.add("desktop-app");
  const theme = () => {
    const t = document.documentElement.getAttribute("data-theme");
    const light = t === "light" || t === "glass" || t === "diamond" || t === "amber" || t === "corporate";            // «Стекло» и пастельные темы светлые независимо от темы Windows
    desktopState({ theme: t === "dark" ? "dark" : light ? "light" : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light" });
  };
  theme();
  new MutationObserver(theme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", theme);
}
