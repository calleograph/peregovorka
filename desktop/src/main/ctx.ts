// Общие зависимости главного процесса (вместо глобальных переменных): создаются в main.ts и передаются модулям.
import type { Policy } from "../common/policy";
import type { Log } from "./log";
import type { SettingsStore, Settings } from "./settings";
import type { TrustStore } from "./trust";

export interface Ctx {
  version: string;
  buildKind: string;                  // release | test
  install: "msi" | "exe" | "dev";     // MSI — корпоративная установка (обновляет администратор)
  userData: string;
  log: Log;
  settings: SettingsStore;
  policy: Policy;
  trust: TrustStore;
  eff(): Settings;                    // настройки с учётом политик
  flags: { allowHttpLoopback: boolean; e2e: boolean; hidden: boolean; devTools: boolean };
  quitting: boolean;
  emit(): void;                       // состояние изменилось — оболочке, трею, значку
}
