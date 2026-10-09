/** Подсказка «куда смотреть», когда браузер показывает системный запрос доступа к микрофону/камере.
 *  Запрос рисует сам браузер (из страницы его не нажать и не передвинуть), поэтому подсказка только указывает направление.
 *  Положение запроса зависит от браузера и ОС — жёстко к одной точке не привязываемся; не удалось определить — нейтральная плашка без стрелки. */

export type BrowserKind = "chromium" | "firefox" | "safari" | "mobile" | "unknown";
export type ArrowAt = "top-left" | "top-center" | "none";

export interface HintPlan { kind: BrowserKind; arrow: ArrowAt; title: string; text: string }

const TITLE = "Разрешите доступ к микрофону и камере в окне браузера";
const WHY = "Без этого вы не сможете использовать микрофон и камеру во встрече";
const NEUTRAL = "Посмотрите на запрос браузера на доступ к камере и микрофону";

export function detectBrowser(ua: string): BrowserKind {
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return "mobile";
  if (/Firefox\//.test(ua) && !/Seamonkey/i.test(ua)) return "firefox";
  // Chrome, Edge, Opera, Яндекс.Браузер и прочие на Chromium показывают запрос у адресной строки слева
  if (/Chrome\/|Chromium\/|Edg\/|OPR\/|YaBrowser\//.test(ua)) return "chromium";
  if (/Safari\//.test(ua) && /Version\//.test(ua)) return "safari";
  return "unknown";
}

export function planFor(ua: string): HintPlan {
  const kind = detectBrowser(ua);
  switch (kind) {
    case "chromium": case "firefox": return { kind, arrow: "top-left", title: TITLE, text: WHY };         // окно у значка слева от адреса
    case "safari": return { kind, arrow: "top-center", title: TITLE, text: WHY };                          // окно под адресной строкой ближе к центру
    case "mobile": return { kind, arrow: "none", title: TITLE, text: WHY };                                // системное окно появляется по центру или снизу
    default: return { kind, arrow: "none", title: NEUTRAL, text: WHY };
  }
}

/** Инструкция, когда доступ заблокирован (нажато «Блокировать» или запрет остался с прошлого раза). */
export function deniedText(mic: boolean, cam: boolean): string {
  const what = mic && cam ? "микрофону и камере" : mic ? "микрофону" : "камере";
  return `Доступ к ${what} заблокирован. Нажмите значок разрешений возле адресной строки браузера и разрешите доступ, затем нажмите «Проверить снова».`;
}

/** Показывать подсказку, только если запрос «завис» дольше этого времени: при уже выданном доступе браузер отвечает мгновенно, мигать не нужно. */
export const HINT_DELAY_MS = 450;
