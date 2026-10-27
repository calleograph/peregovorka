// Визуальная CAPTCHA: связь между сетевым слоем (api.ts) и окном ввода (components/CaptchaDialog.tsx). Окно регистрирует обработчик один раз при запуске приложения.
export interface ImageChallenge { kind: "image"; id: string; image: string; ttl: number; length: number }
export interface CaptchaAnswer { id: string; answer: string }
export type CaptchaHandler = (challenge: ImageChallenge, note: string) => Promise<CaptchaAnswer | null>;

let handler: CaptchaHandler | null = null;
export const setCaptchaHandler = (h: CaptchaHandler | null): void => { handler = h; };

/** Показать картинку и дождаться ответа пользователя; null — отказался или окно не зарегистрировано. */
export const askCaptcha = (challenge: ImageChallenge, note = ""): Promise<CaptchaAnswer | null> => (handler ? handler(challenge, note) : Promise.resolve(null));

const NOTES: Record<string, string> = { wrong: "Символы введены неверно. Попробуйте ещё раз по новой картинке.", expired: "Срок картинки истёк. Введите символы с новой.", context: "Картинка не подходит для этого запроса. Введите символы с новой." };
export const captchaNote = (reason?: unknown): string => (typeof reason === "string" ? NOTES[reason] ?? "" : "");
export const isImageChallenge = (c: unknown): c is ImageChallenge => !!c && typeof c === "object" && (c as { kind?: string }).kind === "image" && typeof (c as { id?: unknown }).id === "string" && typeof (c as { image?: unknown }).image === "string";
