/**
 * Сведения о клиенте для журнала диагностики: сеть, экран, оборудование, список устройств, состояние разрешений.
 * Всё — только технические характеристики; названия устройств (марка микрофона) нужны, чтобы понять причину «звук пропал / занят».
 * Содержимое разговоров и личные данные не собираются. Любая ошибка сбора молча игнорируется — диагностика не должна мешать работе.
 */
export type Primitive = string | number | boolean | null;
export type Info = Record<string, Primitive | string[]>;

interface NavigatorExt {
  connection?: { effectiveType?: string; downlink?: number; rtt?: number; saveData?: boolean; type?: string };
  deviceMemory?: number;
  hardwareConcurrency?: number;
  userAgentData?: { platform?: string; mobile?: boolean };
}

/** Сеть и аппаратная часть: тип подключения (4g/wifi), задержка, число ядер, память, размер экрана. */
export function collectClientInfo(nav: Navigator = navigator, win: Pick<Window, "screen" | "devicePixelRatio" | "isSecureContext"> = window): Info {
  const n = nav as Navigator & NavigatorExt;
  const c = n.connection;
  const out: Info = {
    online: nav.onLine, secure_context: win.isSecureContext, lang: nav.language,
    cores: n.hardwareConcurrency ?? null, memory_gb: n.deviceMemory ?? null,
    screen: `${win.screen?.width ?? 0}x${win.screen?.height ?? 0}@${win.devicePixelRatio ?? 1}`,
    net_type: c?.effectiveType ?? null, net_rtt_ms: c?.rtt ?? null, net_downlink_mbps: c?.downlink ?? null, net_save_data: c?.saveData ?? null,
    net_medium: c?.type ?? null, mobile: n.userAgentData?.mobile ?? /Mobi|Android/i.test(nav.userAgent),
  };
  return out;
}

export interface DeviceCounts { audioinput: number; audiooutput: number; videoinput: number }

/** Число устройств по видам и (если разрешён доступ) их названия — для разбора «микрофон не найден / не тот микрофон». */
export function summarizeDevices(list: Pick<MediaDeviceInfo, "kind" | "label">[]): Info {
  const count: DeviceCounts = { audioinput: 0, audiooutput: 0, videoinput: 0 };
  const names: Record<string, string[]> = { audioinput: [], videoinput: [] };
  for (const d of list) {
    if (d.kind in count) count[d.kind as keyof DeviceCounts] += 1;
    if ((d.kind === "audioinput" || d.kind === "videoinput") && d.label) names[d.kind].push(d.label.slice(0, 70));
  }
  return { mics: count.audioinput, speakers: count.audiooutput, cameras: count.videoinput, mic_names: names.audioinput.slice(0, 6), camera_names: names.videoinput.slice(0, 4) };
}

/** Состояние разрешений браузера (granted / denied / prompt); если API недоступно — «unknown». */
export async function collectPermissions(nav: Navigator = navigator): Promise<Info> {
  const out: Info = {};
  for (const name of ["microphone", "camera"] as const) {
    try {
      const st = await nav.permissions?.query({ name: name as PermissionName });
      out[`perm_${name}`] = st?.state ?? "unknown";
    } catch { out[`perm_${name}`] = "unknown"; }
  }
  return out;
}

export async function collectDevices(nav: Navigator = navigator): Promise<Info> {
  try { return summarizeDevices(await nav.mediaDevices.enumerateDevices()); } catch { return { mics: null, speakers: null, cameras: null }; }
}

/** Полный набор для события device_inventory/network_info. */
export async function collectAll(): Promise<Info> {
  return { ...collectClientInfo(), ...(await collectDevices()), ...(await collectPermissions()) };
}
