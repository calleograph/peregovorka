// Скачанные файлы: что открывать по нажатию на уведомление, а что только показывать в папке.
// Исполняемые и сценарные файлы не запускаются из уведомления — решение остаётся за человеком (и SmartScreen Windows).
const RISKY = new Set([".exe", ".msi", ".msix", ".msp", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".scr", ".lnk", ".hta", ".jar",
  ".reg", ".cpl", ".pif", ".appx", ".appxbundle", ".url", ".iso", ".img", ".vhd", ".vhdx", ".dll", ".sys", ".chm", ".application", ".gadget", ".inf", ".scf"]);

export function openableAfterDownload(path: string): boolean {
  const name = (path.split(/[\\/]/).pop() ?? "").replace(/[\s.]+$/, "");      // Windows отбрасывает точки и пробелы в конце имени: «a.exe. » — это a.exe
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return false;                                        // без расширения — неизвестно, что это
  return !RISKY.has(name.slice(dot).toLowerCase());
}
