/** Заголовок вкладки по адресу страницы: в списке вкладок и в истории браузера разные страницы различимы, а не «Peregovorka» везде. */
const BRAND = "Peregovorka";

export function pageTitle(pathname: string, signedIn = true, brand: string = BRAND): string {
  const p = pathname.replace(/\/+$/, "") || "/";
  let t: string | null = null;
  if (p === "/privacy") t = "Обработка данных";
  else if (p.startsWith("/legal/")) t = "Документ";
  else if (p.startsWith("/guest/")) t = "Гостевой вход";
  else if (!signedIn) t = "Вход";
  else if (p === "/") t = "Переговорки";
  else if (p.startsWith("/rooms/")) t = "Комната";
  else if (p === "/history") t = "История встреч";
  else if (p.startsWith("/history/")) t = "Встреча";
  else if (p === "/profile") t = "Личный кабинет";
  else if (p.startsWith("/admin")) t = "Администрирование";
  return t ? `${t} · ${brand}` : brand;
}

/** Значок вкладки: во время встречи — с красной точкой (видно среди других вкладок). Без навигации и перезагрузки: меняется только адрес значка. */
export function applyFavicon(live: boolean, doc: Document = document): void {
  const links = Array.from(doc.querySelectorAll<HTMLLinkElement>('link[rel="icon"]'));
  for (const l of links) {
    const svg = (l.getAttribute("type") ?? "") === "image/svg+xml";
    const sized = l.getAttribute("sizes") === "32x32";
    if (svg) l.setAttribute("href", live ? "/favicon-live.svg" : "/favicon.svg");
    else if (sized) l.setAttribute("href", live ? "/favicon-live-32.png" : "/favicon-32.png");
  }
}
