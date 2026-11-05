// Скачанные файлы: что открывать по нажатию на уведомление, а что только показывать в папке.
// Список разрешённых, а не запрещённых типов: Windows запускает десятки расширений (.msc, .wsc, .xll, .appinstaller…), полный чёрный список составить нельзя.
// Остальное откроет сам человек из папки — решение за ним (и за SmartScreen Windows).
const SAFE = new Set([".pdf", ".txt", ".md", ".csv", ".rtf", ".odt", ".ods", ".odp", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt",
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp3", ".m4a", ".wav", ".ogg", ".mp4", ".webm", ".mov", ".mkv", ".zip", ".drawio"]);

export function openableAfterDownload(path: string): boolean {
  const name = (path.split(/[\\/]/).pop() ?? "").replace(/[\s.]+$/, "");      // Windows отбрасывает точки и пробелы в конце имени: «a.exe. » — это a.exe
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return false;                                        // без расширения — неизвестно, что это
  return SAFE.has(name.slice(dot).toLowerCase());
}

/** Доступ страницы сервера к микрофону и камере. Сервер добавляет пользователь, поэтому он не считается доверенным: первое обращение подтверждает человек. Серверы из политики организации уже одобрены администратором. */
export function mediaAccess(rec: { managed: boolean; mediaOk?: boolean }, deniedThisRun: boolean): "allow" | "deny" | "ask" {
  if (rec.managed || rec.mediaOk) return "allow";
  return deniedThisRun ? "deny" : "ask";
}
