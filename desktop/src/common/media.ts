// Звук и видео: сохраняемые предпочтения, определение вида устройства по названию, обнаружение подключения и отключения и разбор причины, по которой микрофон не открылся.
// Чистые функции без Electron и DOM: одни и те же правила работают в оболочке и проверяются тестами в Node.
//
// Устройства запоминаются по НАЗВАНИЮ, а не по идентификатору: Chromium выдаёт идентификаторы устройств отдельно для каждого адреса сайта, поэтому идентификатор,
// полученный в окне настроек, не подошёл бы странице организации. Название у одного и того же устройства везде одинаково.

export type NoiseMode = "off" | "standard" | "enhanced";
export interface MediaPrefs {
  /** Название выбранного устройства; пусто — «по умолчанию в Windows». */
  mic: string;
  speaker: string;
  camera: string;
  noise: NoiseMode;
  echo: boolean;
  agc: boolean;
}

export const DEFAULT_MEDIA: MediaPrefs = { mic: "", speaker: "", camera: "", noise: "enhanced", echo: true, agc: true };
const NOISE: readonly NoiseMode[] = ["off", "standard", "enhanced"];

/** Проверка и приведение настроек от интерфейса: неизвестные поля отбрасываются, названия укорачиваются, лишние управляющие символы убираются. */
export function cleanMedia(raw: unknown, base: MediaPrefs = DEFAULT_MEDIA): MediaPrefs {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const name = (v: unknown, d: string) => (typeof v === "string" ? v.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 200) : d);
  return {
    mic: name(o.mic, base.mic), speaker: name(o.speaker, base.speaker), camera: name(o.camera, base.camera),
    noise: NOISE.includes(o.noise as NoiseMode) ? (o.noise as NoiseMode) : base.noise,
    echo: typeof o.echo === "boolean" ? o.echo : base.echo,
    agc: typeof o.agc === "boolean" ? o.agc : base.agc,
  };
}

// ------------------------------------------------------------------------------------------ вид устройства
export type DeviceKind = "bluetooth" | "usb" | "builtin" | "virtual" | "other";

/** Вид устройства по названию. Это подсказка для пользователя, а не сведения от Windows: название задаёт драйвер и может быть любым. */
export function deviceKind(label: string): DeviceKind {
  const l = label.toLowerCase();
  if (/bluetooth|hands[- ]?free|airpods|\bbt\b/.test(l)) return "bluetooth";
  if (/virtual|vb-audio|voicemeeter|stereo mix|стерео микшер|cable (input|output)|obs\b|loopback/.test(l)) return "virtual";
  if (/\busb\b/.test(l)) return "usb";
  if (/realtek|intel|smart sound|internal|built-?in|array|встроен|нешн|high definition audio/.test(l)) return "builtin";
  return "other";
}

export const KIND_TEXT: Record<DeviceKind, string> = { bluetooth: "по названию — Bluetooth", usb: "по названию — USB", builtin: "по названию — встроенное", virtual: "по названию — виртуальное", other: "" };

/** Особенности вида устройства, о которых полезно знать до встречи. Показываются как подсказка, а не как ошибка. */
export function kindAdvice(kind: DeviceKind, role: "mic" | "speaker" | "camera"): string | null {
  if (kind === "bluetooth" && role === "mic") return "Bluetooth-гарнитура при включённом микрофоне переходит в режим «Hands-Free»: звук в наушниках становится хуже. Для встреч это нормально; если качество важно, используйте микрофон ноутбука или USB-гарнитуру.";
  if (kind === "virtual") return "Это виртуальное устройство: его создала программа (например, микшер). Убедитесь, что она запущена, иначе звука не будет.";
  return null;
}

// ------------------------------------------------------------------------------------------ подключение и отключение
export interface DevInfo { kind: "audioinput" | "audiooutput" | "videoinput"; label: string }
const KIND_WORD: Record<DevInfo["kind"], string> = { audioinput: "Микрофон", audiooutput: "Устройство воспроизведения", videoinput: "Камера" };

/** Что изменилось в списке устройств. Названия до выдачи разрешения пустые: тогда сравниваются только количества. */
export function diffDevices(before: DevInfo[], after: DevInfo[]): { added: string[]; removed: string[] } {
  const count = (list: DevInfo[], k: DevInfo["kind"]) => list.filter((d) => d.kind === k).length;
  const out = { added: [] as string[], removed: [] as string[] };
  for (const kind of ["audioinput", "audiooutput", "videoinput"] as const) {
    const b = before.filter((d) => d.kind === kind), a = after.filter((d) => d.kind === kind);
    const named = [...b, ...a].every((d) => d.label);
    if (named) {
      const key = (d: DevInfo) => d.label;
      for (const d of a) if (!b.some((x) => key(x) === key(d))) out.added.push(`${KIND_WORD[kind]} подключён: ${d.label}`);
      for (const d of b) if (!a.some((x) => key(x) === key(d))) out.removed.push(`${KIND_WORD[kind]} отключён: ${d.label}`);
    } else if (count(after, kind) > count(before, kind)) out.added.push(`${KIND_WORD[kind]}: подключено новое устройство`);
    else if (count(after, kind) < count(before, kind)) out.removed.push(`${KIND_WORD[kind]}: устройство отключено`);
  }
  return out;
}

/** Найти устройство по сохранённому названию; пустое название или отсутствие устройства — null («по умолчанию»). */
export function findByLabel<T extends { label: string }>(list: T[], wanted: string): T | null {
  if (!wanted) return null;
  return list.find((d) => d.label === wanted) ?? null;
}

// ------------------------------------------------------------------------------------------ почему не открылся микрофон
export interface MicProblemInput {
  platform: string;                       // process.platform
  error: { name?: string; message?: string } | null;
  /** Разрешение системы для микрофона (Electron `getMediaAccessStatus`): granted, denied, restricted, not-determined, unknown. */
  osAccess: string;
  micCount: number;                       // сколько микрофонов видит Windows
  selectedMissing: boolean;               // выбранного микрофона больше нет в списке
  /** Защитные программы из Центра безопасности Windows (названия), если известны. */
  securityProducts?: string[];
}
export type MicAction = "open_mic_settings" | "open_sound_settings" | "open_privacy_mic" | "retry";
export interface MicProblem {
  cause: "none" | "os_privacy" | "denied" | "security_product" | "no_device" | "selected_missing" | "removed" | "busy" | "start_failed" | "constraints" | "unknown";
  /** certain — причина установлена по признаку системы; possible — самый вероятный вариант из нескольких, а не доказанный факт. */
  confidence: "certain" | "possible";
  title: string;
  text: string;
  steps: string[];
  actions: MicAction[];
  /** Показывать рекомендацию о перезагрузке после изменения параметров монопольного режима. */
  reboot: boolean;
}

export const EXCLUSIVE_STEPS = [
  "Откройте «Настройки микрофона Windows» (кнопка выше) — откроется окно «Звук», вкладка «Запись».",
  "Выберите нужный микрофон → «Свойства» → вкладка «Дополнительно».",
  "В разделе «Монопольный режим» снимите флажок «Разрешить приложениям использовать устройство в монопольном режиме». Если монопольный режим нужен другой программе, оставьте его и снимите только «Приоритет приложения в монопольном режиме».",
  "Нажмите «ОК» и закройте программы, которые могли занять микрофон: другие конференции, запись звука, мессенджеры.",
];

/** Рекомендация после изменения параметров монопольного доступа. Перезагрузка не обязательна во всех случаях: часть изменений применяется после повторного открытия устройства или перезапуска программы. */
export const REBOOT_HINT = "После изменения этих параметров рекомендуется перезагрузить компьютер, чтобы изменения корректно применились и оборудование повторно инициализировалось. Часто достаточно закрыть программу, которая использовала микрофон, и открыть устройство заново, но если проблема осталась — перезагрузите компьютер.";

const none: MicProblem = { cause: "none", confidence: "certain", title: "", text: "", steps: [], actions: [], reboot: false };

/** Причина, по которой микрофон не открылся, и что делать. Не считает любую ошибку доказательством монопольного режима: причиной бывают разрешения Windows, драйвер, отключённое устройство или другая программа. */
export function diagnoseMic(i: MicProblemInput): MicProblem {
  if (!i.error) return none;
  const name = i.error.name ?? "";
  const win = i.platform === "win32";
  const acts = (...a: MicAction[]) => (win ? a : a.filter((x) => x === "retry"));

  if (name === "NotAllowedError" || name === "SecurityError") {
    if (i.osAccess === "denied" || i.osAccess === "restricted") {
      return { cause: "os_privacy", confidence: "certain", title: "Windows не разрешает приложениям доступ к микрофону",
        text: "В параметрах конфиденциальности Windows доступ к микрофону для классических приложений выключен или ограничен политикой организации.",
        steps: ["Откройте «Параметры конфиденциальности микрофона» (кнопка ниже).", "Включите «Доступ к микрофону» и «Разрешить классическим приложениям доступ к микрофону».", "Если переключатели недоступны, параметр задаёт администратор организации."],
        actions: acts("open_privacy_mic", "retry"), reboot: false };
    }
    if (/by system|system permissions/i.test(i.error.message ?? "")) {
      return { cause: "security_product", confidence: "possible", title: "Доступ к микрофону запрещён на уровне системы",
        text: `Windows вернула отказ в доступе к устройству (E_ACCESSDENIED), хотя параметры конфиденциальности разрешают доступ. Так бывает, когда доступ блокирует защитная программа${kasperskyIn(i) ? " — в системе работает " + kasperskyIn(i) : ""} или политика организации.`,
        steps: securitySteps(i), actions: acts("open_privacy_mic", "retry"), reboot: false };
    }
    return { cause: "denied", confidence: "possible", title: "Доступ к микрофону не выдан",
      text: "Приложению отказано в доступе к микрофону. Системные параметры конфиденциальности при этом не запрещают доступ, поэтому причина — запрос был отклонён самим приложением или политикой.",
      steps: ["Повторите попытку: запрос не должен повторяться, если он не показывается.", "Проверьте параметры конфиденциальности микрофона в Windows.", "Если проблема остаётся, сохраните отчёт («Диагностика») и передайте администратору."],
      actions: acts("open_privacy_mic", "retry"), reboot: false };
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") {
    if (i.selectedMissing && i.micCount > 0) {
      return { cause: "selected_missing", confidence: "certain", title: "Выбранный микрофон не найден",
        text: "Устройство, которое вы выбирали, больше не подключено или отключено в Windows. Остальные микрофоны доступны.",
        steps: ["Выберите другой микрофон в списке или «По умолчанию в Windows».", "Если это беспроводная гарнитура, проверьте, что она включена и подключена."],
        actions: acts("retry"), reboot: false };
    }
    if (i.micCount === 0) {
      return { cause: "no_device", confidence: "certain", title: "Windows не видит ни одного микрофона",
        text: "Микрофон не подключён, отключён в Windows или для него не установлен драйвер.",
        steps: ["Подключите микрофон или гарнитуру.", "Откройте «Настройки микрофона Windows» → вкладка «Запись», щёлкните правой кнопкой по пустому месту и включите «Показать отключённые устройства».", "Если устройство отключено, выберите «Включить».", "Для встроенного микрофона проверьте драйвер в «Диспетчере устройств»."],
        actions: acts("open_mic_settings", "open_sound_settings", "retry"), reboot: false };
    }
    return { cause: "constraints", confidence: "possible", title: "Микрофон не поддерживает выбранные параметры",
      text: "Устройство найдено, но не приняло заданные параметры обработки звука.", steps: ["Выберите другой микрофон или «По умолчанию в Windows»."], actions: acts("retry"), reboot: false };
  }
  const msg = i.error.message ?? "";
  if ((name === "NotReadableError" || name === "TrackStartError") && /was removed/i.test(msg)) {
    return { cause: "removed", confidence: "certain", title: "Микрофон отключён",
      text: "Windows сообщила, что устройство удалено или стало недействительным (отключили гарнитуру, перезапустился драйвер).",
      steps: ["Подключите микрофон и нажмите «Повторить» или выберите другой микрофон."], actions: acts("open_mic_settings", "retry"), reboot: false };
  }
  // Chromium 152+: «Device in use» — только AUDCLNT_E_DEVICE_IN_USE; любой другой отказ запуска — «Could not start audio source» (не занятость)
  if ((name === "NotReadableError" || name === "TrackStartError") && msg && !/in use/i.test(msg)) {
    return { cause: "start_failed", confidence: "possible", title: "Захват звука не запустился",
      text: `Устройство найдено, но запустить захват не удалось. Это не сообщение о занятости микрофона. Частые причины: защитная программа не разрешает приложению получать звук${kasperskyIn(i) ? " (в системе работает " + kasperskyIn(i) + ")" : ""}, сбой драйвера, неподдерживаемый формат. Если в Edge этот микрофон работает, наиболее вероятна защитная программа: браузер Microsoft она считает доверенным, а это приложение — нет.`,
      steps: [...securitySteps(i), "Сохраните отчёт во вкладке «Диагностика»: в нём код ошибки Windows (раздел audio) — по нему видно, на каком шаге отказ."],
      actions: acts("open_mic_settings", "open_privacy_mic", "retry"), reboot: false };
  }
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
    return { cause: "busy", confidence: "possible", title: /in use/i.test(msg) ? "Микрофон занят" : "Микрофон не удалось открыть",
      text: "Устройство найдено, но открыть его не получилось. Чаще всего его занимает другая программа, в том числе в «монопольном режиме», но возможны и сбой драйвера, и аппаратное отключение микрофона (например, кнопкой на корпусе). По одной ошибке точную причину установить нельзя.",
      steps: ["Закройте программы, которые могут использовать микрофон: другие конференции, запись звука, голосовые мессенджеры.", "Проверьте, не выключен ли микрофон кнопкой на гарнитуре или клавишей ноутбука.", ...EXCLUSIVE_STEPS],
      actions: acts("open_mic_settings", "open_privacy_mic", "retry"), reboot: true };
  }
  return { cause: "unknown", confidence: "possible", title: "Микрофон не открылся", text: `Причина неизвестна (${name || "ошибка"}). Повторите попытку и проверьте микрофон в настройках Windows.`,
    steps: ["Повторите попытку.", "Выберите другое устройство.", "Сохраните отчёт во вкладке «Диагностика» и передайте его администратору."], actions: acts("open_mic_settings", "retry"), reboot: false };
}

const kasperskyIn = (i: MicProblemInput) => (i.securityProducts ?? []).find((p) => /kaspersky/i.test(p)) ?? "";

/** Шаги при подозрении на защитную программу. Kaspersky Endpoint Security: компонент «Предотвращение вторжений» выдаёт звук с микрофона
 *  только программам из доверенных групп (в KES 12 по умолчанию — только «Доверенные»); неподписанное приложение туда может не попасть. */
function securitySteps(i: MicProblemInput): string[] {
  const k = kasperskyIn(i);
  if (k) return [
    `${k}: администратор защиты разрешает приложению доступ к микрофону — в политике «Предотвращение вторжений» найдите программу Peregovorka (Peregovorka.exe) и разрешите ей доступ к звукозаписывающим устройствам либо поместите её в группу «Доверенные» (надёжнее — по сертификату подписи организации).`,
    "После изменения правил полностью закройте приложение (значок в области уведомлений → «Выход») и откройте снова.",
  ];
  return [
    "Проверьте журнал защитной программы (антивируса) на компьютере: не заблокирован ли доступ приложения Peregovorka к микрофону; при необходимости добавьте его в доверенные.",
    "Проверьте тот же микрофон в Edge: если там работает, причина — в правилах для этого приложения, а не в устройстве.",
  ];
}

// ------------------------------------------------------------------------------------------ журнал звука Chromium (WASAPI)
/** Коды HRESULT, которые Chromium пишет в журнал при открытии микрофона (`IAudioClient::Initialize=[HRESULT: 0x…, …]`), и их смысл. */
export const WASAPI_CODES: Record<string, { name: string; meaning: string }> = {
  "8889000A": { name: "AUDCLNT_E_DEVICE_IN_USE", meaning: "устройство занято: другая программа держит его в монопольном режиме или предыдущий поток ещё не освобождён" },
  "80070005": { name: "E_ACCESSDENIED", meaning: "доступ запрещён: конфиденциальность Windows, политика или защитная программа" },
  "88890004": { name: "AUDCLNT_E_DEVICE_INVALIDATED", meaning: "устройство отключено или стало недействительным" },
  "80070651": { name: "ERROR_DEVICE_REMOVED", meaning: "устройство удалено" },
  "80070490": { name: "ERROR_NOT_FOUND", meaning: "устройство не найдено" },
  "88890008": { name: "AUDCLNT_E_UNSUPPORTED_FORMAT", meaning: "драйвер не принял формат звука" },
  "8889000E": { name: "AUDCLNT_E_EXCLUSIVE_MODE_NOT_ALLOWED", meaning: "монопольный режим запрещён" },
  "88890012": { name: "AUDCLNT_E_EXCLUSIVE_MODE_ONLY", meaning: "устройство работает только в монопольном режиме" },
  "8889000F": { name: "AUDCLNT_E_ENDPOINT_CREATE_FAILED", meaning: "не удалось создать точку подключения устройства (драйвер)" },
  "88890010": { name: "AUDCLNT_E_SERVICE_NOT_RUNNING", meaning: "служба Windows Audio не запущена" },
  "88890017": { name: "AUDCLNT_E_CPUUSAGE_EXCEEDED", meaning: "звуковой движок Windows перегружен" },
  "88890026": { name: "AUDCLNT_E_RESOURCES_INVALIDATED", meaning: "ресурсы устройства сброшены системой" },
  "8007000E": { name: "E_OUTOFMEMORY", meaning: "нехватка памяти" },
};

export interface WasapiFinding { code: string; name: string; meaning: string; count: number; last: string }

/** Ошибки WASAPI из журнала Chromium: какие коды и сколько раз. Строки без «HRESULT: 0x…» не учитываются; неизвестный код показывается как есть. */
export function wasapiFindings(lines: string[]): WasapiFinding[] {
  const by = new Map<string, WasapiFinding>();
  for (const line of lines) {
    if (!/WAIS|AudioInput|IAudioClient/i.test(line)) continue;
    const m = /HRESULT:\s*0x([0-9A-Fa-f]{8})/.exec(line);
    if (!m) continue;
    const code = m[1].toUpperCase();
    const known = WASAPI_CODES[code] ?? { name: `HRESULT 0x${code}`, meaning: "другая ошибка Windows" };
    const f = by.get(code) ?? { code, ...known, count: 0, last: "" };
    f.count++; f.last = line.slice(0, 300);
    by.set(code, f);
  }
  return [...by.values()].sort((a, b) => b.count - a.count);
}

// ------------------------------------------------------------------------------------------ системные настройки Windows
export type SoundSettings = "mic-classic" | "sound" | "privacy-mic" | "privacy-cam";
export const SOUND_SETTINGS: readonly SoundSettings[] = ["mic-classic", "sound", "privacy-mic", "privacy-cam"];

/** Как открыть системные настройки: фиксированные команды Windows без параметров от пользователя. Классическое окно «Звук» (вкладка «Запись») — единственное место, где
 *  меняется монопольный режим; оно открывается библиотекой управления панели. Если вкладку открыть не удалось, `fallback` ведёт в «Параметры → Звук». */
export function soundCommand(kind: SoundSettings): { exe: string; args: string[]; uri?: string; fallback?: string } {
  switch (kind) {
    case "mic-classic": return { exe: "rundll32.exe", args: ["shell32.dll,Control_RunDLL", "mmsys.cpl,,1"], fallback: "ms-settings:sound" };
    case "sound": return { exe: "", args: [], uri: "ms-settings:sound" };
    case "privacy-mic": return { exe: "", args: [], uri: "ms-settings:privacy-microphone" };
    case "privacy-cam": return { exe: "", args: [], uri: "ms-settings:privacy-webcam" };
  }
}

// ------------------------------------------------------------------------------------------ уровень сигнала
const LEVEL_FLOOR_DB = -60;

/** Мгновенный уровень сигнала (0..1) по среднеквадратичному значению, в децибелах от –60 до 0. */
export function levelOf(samples: ArrayLike<number>): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  const rms = Math.sqrt(sum / Math.max(1, samples.length));
  if (rms <= 0) return 0;
  return Math.min(1, Math.max(0, (20 * Math.log10(rms) - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB));
}
