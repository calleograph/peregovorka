// Маскирование секретов в журнале и диагностическом отчёте: токены устройства, cookie, CSRF, пароли, заголовки авторизации, параметры ссылок с ключами.

const RULES: [RegExp, string][] = [
  [/pgd_[A-Za-z0-9_-]{8,}/g, "pgd_***"],
  [/\b(vm_session|session|csrf[_-]?token|x-csrf-token|token|password|passwd|secret|authorization|cookie|set-cookie|api[_-]?key)(["']?\s*[:=]\s*["']?)(Bearer\s+)?[^\s"',;&}]+/gi, "$1$2***"],
  [/\bBearer\s+[A-Za-z0-9._~+/-]+=*/g, "Bearer ***"],
  [/([?&](?:token|key|sig|signature|code|password)=)[^&#\s]+/gi, "$1***"],
  [/\/guest\/[A-Za-z0-9_-]{8,}/g, "/guest/***"],
];

export function redact(text: string): string {
  let s = String(text ?? "");
  for (const [re, to] of RULES) s = s.replace(re, to);
  return s;
}
