/** Администрирование открыто не по публичному адресу экземпляра: браузер, cookie и порты проверяют именно публичный адрес. */
export function originMismatch(openedHost: string, publicHost: string): string | null {
  const a = openedHost.trim().toLowerCase();
  const b = publicHost.trim().toLowerCase();
  if (!a || !b || a === b) return null;
  return `Эта страница открыта по адресу «${a}», а публичный адрес сервиса в настройках — «${b}». Пользователи и гости подключаются по публичному адресу: проверьте, что именно он доступен снаружи.`;
}
