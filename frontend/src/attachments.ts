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
