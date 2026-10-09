/** Тексты уведомления о cookie и страницы «Обработка данных» (их вводит администратор; открываются без входа). Запрашиваются один раз за сеанс страницы. */
export interface Privacy {
  cookie_text: string; operator: string; purpose: string; data_types: string; cookies: string; retention: string; contact: string; policy_url: string;
}

export const DEFAULT_COOKIE_TEXT = "Сервис использует технические cookie, необходимые для авторизации и работы системы.";

let cached: Promise<Privacy> | null = null;

export function loadPrivacy(): Promise<Privacy> {
  cached ??= fetch("/api/v1/public/privacy", { credentials: "same-origin" })
    .then((r) => (r.ok ? (r.json() as Promise<Privacy>) : Promise.reject(new Error("privacy"))))
    .catch(() => ({ cookie_text: DEFAULT_COOKIE_TEXT, operator: "", purpose: "", data_types: "", cookies: "", retention: "", contact: "", policy_url: "" }));
  return cached;
}
