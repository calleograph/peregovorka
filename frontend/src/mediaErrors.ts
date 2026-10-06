/** Понятные причины ошибок устройств, демонстрации экрана и подключения вместо общего «Не удалось переключить устройство». */

export type MediaAction = "mic" | "camera" | "screen" | "device" | "connect" | "playback";

export interface ErrorInfo {
  /** Короткий код для журнала/диагностики (имя ошибки браузера или внутренняя причина). */
  reason: string;
  /** Что увидит пользователь у кнопки, с которой связана ошибка. */
  message: string;
  /** Пользователь сам отменил выбор — это не сбой, показывать красную ошибку не нужно. */
  benign?: boolean;
}

const DEVICE_WORD: Record<MediaAction, string> = {
  mic: "микрофону", camera: "камере", screen: "экрану", device: "устройству", connect: "серверу", playback: "звуку",
};

function lower(e: unknown): string {
  return String((e as { message?: unknown })?.message ?? e ?? "").toLowerCase();
}

export function describeMediaError(e: unknown, action: MediaAction, opts: { secureContext?: boolean } = {}): ErrorInfo {
  const name = String((e as { name?: unknown })?.name ?? "Error");
  const msg = lower(e);
  const secure = opts.secureContext ?? (typeof window === "undefined" ? true : window.isSecureContext);

  if (!secure && action !== "connect") {
    return { reason: "InsecureContext", message: "Страница открыта не по HTTPS: браузер не даёт доступ к микрофону, камере и экрану. Откройте сайт по https:// или обратитесь к администратору." };
  }
  // ошибки публикации/подключения LiveKit определяем по тексту (классы SDK не всегда доступны по имени)
  if (msg.includes("permission") && (msg.includes("publish") || msg.includes("can_publish") || msg.includes("not allowed to"))) {
    return { reason: "PublishNotAllowed", message: "Сервер не разрешил вам публиковать этот источник (ограничено настройками комнаты или ролью)." };
  }
  if (msg.includes("timeout") || msg.includes("timed out")) {
    return { reason: "Timeout", message: action === "connect" ? "Сервер звонков не отвечает (таймаут). Проверьте сеть и повторите." : "Сервер звонков не подтвердил публикацию вовремя (таймаут сети). Повторите попытку." };
  }
  if (msg.includes("signal connection") || msg.includes("websocket") || msg.includes("failed to fetch")) {
    return { reason: "SignalFailed", message: "Нет соединения с сервером звонков. Проверьте сеть, VPN/прокси и адрес сервера; если проблема не уходит — сообщите администратору (возможно, не проходят WebSocket-соединения)." };
  }
  if (msg.includes("duplicate_identity") || msg.includes("duplicate identity")) {
    return { reason: "DuplicateIdentity", message: "Вы вошли в эту комнату с другого устройства или вкладки — это соединение закрыто." };
  }

  switch (name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
      if (action === "screen") {
        return { reason: name, benign: msg.includes("cancel") || msg.includes("dismiss") || msg.includes("denied by user") ? true : false,
          message: "Доступ к экрану не получен: выбор окна/экрана отменён либо запрещён браузером или системой. Нажмите «Показать экран» ещё раз и подтвердите выбор; если окно выбора не появляется — проверьте разрешения сайта и системные настройки записи экрана." };
      }
      return { reason: name, message: `Нет разрешения на доступ к ${DEVICE_WORD[action]}. Разрешите его в настройках сайта (значок замка рядом с адресом) и повторите.` };
    case "NotFoundError":
    case "DevicesNotFoundError":
      return { reason: name, message: action === "screen" ? "Нет доступного источника экрана или окна для показа." : action === "camera" ? "Камера не найдена. Подключите её и обновите список устройств." : "Микрофон не найден. Подключите его и обновите список устройств." };
    case "NotReadableError":
    case "TrackStartError":
      return { reason: name, message: action === "screen"
        ? "Система не позволила захватить экран (источник занят другой программой или запрещён ОС; на macOS разрешите браузеру «Запись экрана»)."
        : `Устройство занято другой программой или недоступно (${action === "camera" ? "камера" : "микрофон"}). Закройте программы, которые его используют, и повторите.` };
    case "OverconstrainedError":
    case "ConstraintNotSatisfiedError":
      return { reason: name, message: "Выбранное устройство не поддерживает нужные параметры. Выберите другое устройство в списке «Устройства»." };
    case "AbortError":
      return { reason: name, benign: action === "screen", message: action === "screen" ? "Выбор экрана отменён." : "Операция прервана. Повторите попытку." };
    case "SecurityError":
      return { reason: name, message: "Браузер запретил доступ политикой безопасности страницы (нужен HTTPS и разрешение сайта на микрофон/камеру/экран)." };
    case "TypeError":
      return { reason: name, message: action === "screen" ? "Браузер не поддерживает показ экрана с такими параметрами (обновите браузер или используйте Chrome/Edge/Firefox актуальной версии)." : "Параметры устройства не приняты браузером." };
    default:
      return { reason: name, message: action === "device" ? `Не удалось переключить устройство (${name}${msg ? `: ${msg.slice(0, 120)}` : ""}). Повторите или выберите другое.`
        : `Не удалось выполнить действие (${name}${msg ? `: ${msg.slice(0, 120)}` : ""}). Повторите попытку.` };
  }
}

/** Причина остановки показа экрана, определяемая по контексту. */
export type ScreenStopReason = "user_button" | "browser_stop" | "connection_lost" | "room_ended" | "unpublished_remotely";

export const SCREEN_STOP_TEXT: Record<ScreenStopReason, string> = {
  user_button: "Вы остановили показ экрана.",
  browser_stop: "Показ экрана остановлен в браузере (кнопка «Остановить доступ» или закрыто окно/вкладка, которую вы показывали).",
  connection_lost: "Показ экрана прервался из-за потери связи с сервером. Нажмите «Показать экран», чтобы начать снова.",
  room_ended: "Показ экрана остановлен: встреча завершена.",
  unpublished_remotely: "Сервер остановил публикацию экрана (например, начал показ другой участник).",
};
