// Пакет Linux (.deb): версия установленных файлов — в resources/package-version (пишет сборка пакета). APT обновляет файлы поверх работающей программы;
// раз в минуту сверяем версию файлов с версией процесса — разошлись, значит установлена новая версия и нужен перезапуск.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Ctx } from "./ctx";

export function installedVersion(resources: string = process.resourcesPath): string | null {
  try {
    const v = readFileSync(join(resources, "package-version"), "utf8").trim();
    return /^\d+\.\d+\.\d+([~+.-][\w.~+-]*)?$/.test(v) ? v : null;
  } catch { return null; }
}

export function watchPackage(ctx: Ctx, changed: (version: string) => void, everyMs = 60_000): NodeJS.Timeout {
  const t = setInterval(() => {
    const v = installedVersion();
    if (v && v !== ctx.version) changed(v);          // пакет удалён (файла нет) — не повод перезапускаться
  }, everyMs);
  t.unref();
  return t;
}
