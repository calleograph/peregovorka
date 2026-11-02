// Linux: чистые правила интеграции с рабочим столом (проверяются тестами без Electron).
import type { Autostart } from "./policy";

/** Сеанс Wayland: захват экрана — только через системное окно выбора (XDG Desktop Portal + PipeWire), миниатюр всех окон приложению не дают. */
export function waylandSession(env: Record<string, string | undefined>, platform: string): boolean {
  return platform === "linux" && (env.XDG_SESSION_TYPE === "wayland" || !!env.WAYLAND_DISPLAY) && env.ELECTRON_OZONE_PLATFORM_HINT !== "x11";
}

/** Автозапуск по стандарту XDG (~/.config/autostart/peregovorka.desktop читают GNOME, KDE, Xfce, Cinnamon, MATE); «в трей» — с ключом --hidden. */
export function autostartEntry(mode: Autostart): string | null {
  if (mode === "off") return null;
  return ["[Desktop Entry]", "Type=Application", "Name=Peregovorka", "Comment=Видеовстречи и чаты организации",
    `Exec=peregovorka${mode === "tray" ? " --hidden" : ""}`, "Icon=peregovorka", "Terminal=false", "X-GNOME-Autostart-enabled=true", ""].join("\n");
}
