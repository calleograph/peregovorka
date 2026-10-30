/** Подписи вложений чата: размер и тип файла. */
export function formatSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`;
  return `${Math.max(1, Math.round(n / 1024))} КБ`;
}

/** Расширение для подписи типа файла: «PDF», «DOCX»… */
export function fileTypeLabel(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 && i < name.length - 1 ? name.slice(i + 1).toUpperCase().slice(0, 6) : "файл";
}

/** Имя для вставленного из буфера обмена скриншота (у такого файла имя пустое или «image.png»). */
export function pastedName(name: string, mime: string, when: Date, index = 0, total = 1): string {
  if (name && name !== "image.png") return name;
  const ext = (mime.split("/")[1] || "png").replace("jpeg", "jpg");
  const stamp = when.toLocaleTimeString("ru-RU").replace(/:/g, "-");
  return `Скриншот ${stamp}${total > 1 ? `-${index + 1}` : ""}.${ext}`;
}

/**
 * Файлы из буфера обмена при вставке. Скриншот обычно приходит файлом (`files`), но часть программ Windows (Office, Teams, почтовые клиенты, «копировать изображение» в браузерах)
 * кладёт в буфер только HTML с картинкой внутри (`<img src="data:image/png;base64,…">`) или даёт элемент-файл без списка `files`: берём и такие.
 */
export function pastedFiles(data: Pick<DataTransfer, "files" | "items" | "getData"> | null | undefined): File[] {
  if (!data) return [];
  const files = Array.from(data.files ?? []);
  if (files.length) return files;
  const fromItems = Array.from(data.items ?? []).filter((i) => i.kind === "file").map((i) => i.getAsFile()).filter((f): f is File => !!f);
  if (fromItems.length) return fromItems;
  let html = "";
  try { html = data.getData("text/html"); } catch { /* форматы недоступны */ }
  const out: File[] = [];
  for (const m of html.matchAll(/<img[^>]+src=["']data:(image\/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)["']/gi)) {
    try {
      const bin = atob(m[2].replace(/\s+/g, ""));
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      if (bytes.length) out.push(new File([bytes], "image." + m[1].split("/")[1].replace("jpeg", "jpg"), { type: m[1] }));
    } catch { /* повреждённая картинка в HTML — пропускаем */ }
    if (out.length >= 5) break;
  }
  return out;
}
