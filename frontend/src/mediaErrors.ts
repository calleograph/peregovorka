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

/** Устройство занято другой программой (в т. ч. в «монопольном» режиме Windows) или недоступно для запуска. */
export function isDeviceBusyError(e: unknown): boolean {
  const name = String((e as { name?: unknown })?.name ?? "");
  return name === "NotReadableError" || name === "TrackStartError" || /could not start (audio|video) source|device in use|in use by another/i.test(lower(e));
}

/** Не удалось установить медиасоединение (ICE/PeerConnection): сеть, закрытые порты, VPN/прокси, антивирус. */
export function isIceError(e: unknown): boolean {
  return /pc connection|ice (connection|failed)|peerconnection|ice_failed/.test(lower(e));
}

/** Сбой подключения, после которого есть смысл повторить попытку автоматически (сеть/таймаут/ICE), а не показывать ошибку сразу. */
export function isTransientConnectError(e: unknown): boolean {
  return isIceError(e) || /timeout|timed out|signal connection|websocket|failed to fetch|network|connection.reset|econnreset/.test(lower(e));
}

/** Соединение сброшено на сетевом уровне (ERR_CONNECTION_RESET / ECONNRESET): граница сети, а не ошибка приложения. */
export function isConnectionReset(e: unknown): boolean {
  return /err_connection_reset|connection reset|econnreset|connection_reset/.test(lower(e));
}

const NET_CAUSES = "VPN (в том числе слишком большой MTU туннеля — при проблемах попробуйте MTU 1250–1400), прокси, файрвол или антивирус";

export function describeMediaError(e: unknown, action: MediaAction, opts: { secureContext?: boolean; stage?: "server" | "media" } = {}): ErrorInfo {
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
  if (action === "connect" && isConnectionReset(e)) {
    return { reason: "ConnectionReset", message: `Соединение с сервером звонков сброшено на сетевом уровне (ERR_CONNECTION_RESET) — это не ошибка приложения. Возможные причины: ${NET_CAUSES} между вами и сервером. Попробуйте другую сеть или отключите VPN; администратору — проверьте MTU VPN и передачу WebSocket на прокси (сведения о вашем соединении записаны в журнал).` };
  }
  if (isIceError(e)) {
    return { reason: "IceFailed", message: "Не удалось установить медиасоединение со звонковым сервером (ICE). Обычно это закрытые порты UDP/TCP, VPN или прокси, строгий файрвол или антивирус. Нажмите «Войти в комнату» ещё раз; если повторяется — сообщите администратору (сведения о вашей сети уже записаны в журнал)." };
  }
  if (msg.includes("timeout") || msg.includes("timed out")) {
    return { reason: opts.stage === "media" ? "IceTimeout" : "Timeout", message: action === "connect"
      ? (opts.stage === "media" ? "Сигнальное соединение установлено, но путь для звука и видео (ICE) не согласовался вовремя: вероятно, закрыты порты медиа UDP/TCP либо мешает " + NET_CAUSES + "."
        : "Сервер звонков не отвечает на сигнальное соединение (таймаут). Проверьте сеть, VPN/прокси и повторите.") : "Сервер звонков не подтвердил публикацию вовремя (таймаут сети). Повторите попытку." };
  }
  if (msg.includes("signal connection") || msg.includes("websocket") || msg.includes("failed to fetch")) {
    return { reason: "SignalFailed", message: `Не удалось установить сигнальное соединение (WebSocket) с сервером звонков. Проверьте сеть и адрес сервера. Возможные причины: ${NET_CAUSES}, либо прокси не передаёт WebSocket. Если не уходит — сообщите администратору.` };
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
        : action === "camera"
          ? "Камера занята другой программой или недоступна. Закройте программы, которые её используют (другие конференции, видеозапись), и повторите."
          : "Микрофон занят другой программой или недоступен. Закройте программы, которые его используют (другие конференции, запись звука), или выберите другое устройство в списке «Устройства». В Windows микрофон может быть захвачен в «монопольном режиме»: Параметры звука → Устройство ввода → Свойства → Дополнительно → снимите «Разрешить приложениям использовать устройство в монопольном режиме». Вы можете остаться в комнате без микрофона и включить его позже." };
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
