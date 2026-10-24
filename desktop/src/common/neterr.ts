// Ошибки сети Chromium (net::ERR_*) → понятное сообщение для человека и подсказка для администратора. Без трассировок.

export type NetProblem = { code: string; title: string; hint: string; kind: "dns" | "offline" | "unreachable" | "tls" | "proxy" | "timeout" | "other" };

const T: [RegExp, Omit<NetProblem, "code">][] = [
  [/ERR_NAME_NOT_RESOLVED|ERR_NAME_RESOLUTION_FAILED/, { kind: "dns", title: "Сервер не найден", hint: "Проверьте адрес. Если адрес верный — имя сервера не находится в DNS: возможно, нужен VPN или корпоративная сеть." }],
  [/ERR_INTERNET_DISCONNECTED|ERR_NETWORK_CHANGED/, { kind: "offline", title: "Нет подключения к сети", hint: "Проверьте подключение к интернету или корпоративной сети." }],
  [/ERR_CONNECTION_REFUSED|ERR_CONNECTION_RESET|ERR_CONNECTION_CLOSED|ERR_ADDRESS_UNREACHABLE|ERR_CONNECTION_FAILED|ERR_EMPTY_RESPONSE/, { kind: "unreachable", title: "Сервер недоступен", hint: "Сервер не принимает подключения. Попробуйте позже или сообщите администратору." }],
  [/ERR_TIMED_OUT|ERR_CONNECTION_TIMED_OUT/, { kind: "timeout", title: "Сервер не отвечает", hint: "Сервер не ответил вовремя. Проверьте сеть, VPN и настройки прокси." }],
  [/ERR_CERT_AUTHORITY_INVALID/, { kind: "tls", title: "Сертификат не доверен", hint: "Сертификат сервера выдан центром сертификации, которому Windows не доверяет. Администратору: установите корневой сертификат организации в хранилище «Доверенные корневые центры сертификации» (групповой политикой)." }],
  [/ERR_CERT_COMMON_NAME_INVALID/, { kind: "tls", title: "Сертификат выдан на другое имя", hint: "Имя в сертификате не совпадает с адресом сервера. Проверьте адрес или сообщите администратору." }],
  [/ERR_CERT_DATE_INVALID/, { kind: "tls", title: "Срок действия сертификата истёк", hint: "Сертификат сервера просрочен или ещё не действует. Проверьте дату на компьютере; если она верна — сообщите администратору сервера." }],
  [/ERR_CERT_REVOKED/, { kind: "tls", title: "Сертификат отозван", hint: "Сертификат сервера отозван выдавшим его центром. Подключение небезопасно." }],
  [/ERR_CERT_|ERR_SSL_|ERR_BAD_SSL|ERR_CERTIFICATE/, { kind: "tls", title: "Ошибка защищённого соединения", hint: "Не удалось установить защищённое соединение с сервером. Сообщите администратору код ошибки." }],
  [/ERR_PROXY|ERR_TUNNEL_CONNECTION_FAILED|ERR_MANDATORY_PROXY/, { kind: "proxy", title: "Ошибка прокси-сервера", hint: "Не удалось подключиться через прокси. Проверьте параметры прокси в настройках приложения или Windows." }],
];

export function describeNetError(err: unknown): NetProblem {
  const s = String((err as { message?: string })?.message ?? err ?? "");
  const code = /net::(ERR_[A-Z_]+)/.exec(s)?.[1] ?? /(ERR_[A-Z_]+)/.exec(s)?.[1] ?? "UNKNOWN";
  for (const [re, p] of T) if (re.test(code)) return { code, ...p };
  return { code, kind: "other", title: "Не удалось подключиться к серверу", hint: "Повторите попытку. Если ошибка повторяется — откройте «Диагностика» и отправьте отчёт администратору." };
}

/** Задержка следующей попытки переподключения: нарастающая с разбросом, первая через ~2 с, дальше вдвое дольше, не реже раза в 5 мин. */
export function backoffMs(attempt: number, rnd: number = Math.random()): number {
  const base = Math.min(300_000, 2000 * 2 ** Math.max(0, Math.min(attempt, 12)));
  return Math.round(base * (0.8 + 0.4 * rnd));
}
