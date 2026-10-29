import { PROJECT_URL } from "./projectUrl";

/** Текущий выпуск приложения для Android. Версию сверяет тест с mobile/android/VERSION и README; перед публикацией сервера скрипт
 *  scripts/check_download_links.py проверяет, что выпуск на GitHub опубликован и содержит APK. Ссылка ведёт на страницу выпуска, а не на файл:
 *  имя APK зависит от ключа подписи, и прямой адрес мог бы указать на отсутствующий файл. */
export const ANDROID_VERSION = "0.1.1";
export const ANDROID_RELEASE_URL = `${PROJECT_URL}/releases/tag/android-v${ANDROID_VERSION}`;
