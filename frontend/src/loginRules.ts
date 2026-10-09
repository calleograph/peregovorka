/** Допустимый формат логина (зеркало проверки на сервере): буквы любых алфавитов, цифры и символы . _ - ; формы user, DOMAIN\user, user@domain. */
const PART = "[\\p{L}\\p{N}_][\\p{L}\\p{N}_.\\-]{0,63}";
const DOMAIN = "[\\p{L}\\p{N}_][\\p{L}\\p{N}_.\\-]{0,252}";
const RE = new RegExp(`^(?:${PART}\\\\)?${PART}(?:@${DOMAIN})?$`, "u");

export function isValidLogin(raw: string): boolean {
  const v = raw.trim();
  return v.length > 0 && v.length <= 256 && RE.test(v);
}

export const LOGIN_HINT = "В логине допустимы буквы, цифры и символы . _ - (например, ivanov). Пробелы, кавычки и скобки использовать нельзя.";
