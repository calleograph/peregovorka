import { PROJECT_URL } from "./projectUrl";

/** Текущий выпуск приложения для Windows. Строку с версией переписывает скрипт выпуска клиента (desktop/scripts/release.mjs), тест сверяет её с desktop/package.json. */
export const DESKTOP_VERSION = "1.3.1";
export const DESKTOP_SETUP_URL = `${PROJECT_URL}/releases/download/desktop-v${DESKTOP_VERSION}/Peregovorka-Setup-${DESKTOP_VERSION}.exe`;
