// Адрес app://shell/… → файл оболочки внутри пакета приложения и его file:-адрес. Адрес строится только через pathToFileURL: в 1.0.0 он склеивался
// вручную («file://» + путь), на Windows это давало file://C%3A/… (буква диска становилась именем хоста), и окно вместо мастера показывало «not found».
import nodePath from "node:path";
import { pathToFileURL } from "node:url";

export function shellFile(root: string, reqUrl: string, path: typeof nodePath = nodePath): { file: string; url: string } | null {
  let u: URL;
  let rel: string;
  try { u = new URL(reqUrl); rel = decodeURIComponent(u.pathname); } catch { return null; }
  if (u.protocol !== "app:" || u.host !== "shell") return null;
  const file = path.normalize(path.join(root, rel));
  if (!file.startsWith(root + path.sep)) return null;                         // ../ и прочие выходы за каталог оболочки
  return { file, url: pathToFileURL(file, { windows: path === nodePath.win32 }).href };
}
