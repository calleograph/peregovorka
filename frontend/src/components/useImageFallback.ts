import { useState } from "react";

/** Сколько раз просить картинку заново, прежде чем сдаться (значение — число попыток после первой). */
export const IMAGE_RETRIES = 1;

/** Адрес очередной попытки: первая — как есть, повторы — с меткой `r=N` (новый запрос вместо устаревшего ответа). */
export function retryUrl(url: string, tries: number): string {
  return tries === 0 ? url : `${url}${url.includes("?") ? "&" : "?"}r=${tries}`;
}

/**
 * Картинка по адресу сервера: при ошибке загрузки — повторная попытка (сбой сети, обновление сеанса, устаревший ответ в кэше), затем `failed`:
 * вызывающий показывает стандартный значок вместо «сломанной картинки». Другой адрес начинает счёт заново.
 */
export function useImageFallback(url: string | null | undefined): { src: string | null; failed: boolean; onError: () => void } {
  const [st, setSt] = useState<{ url: string | null | undefined; n: number }>({ url, n: 0 });
  const n = st.url === url ? st.n : 0;
  if (!url) return { src: null, failed: true, onError: () => undefined };
  return { src: retryUrl(url, n), failed: n > IMAGE_RETRIES, onError: () => setSt({ url, n: n + 1 }) };
}
