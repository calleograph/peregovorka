// «Настройки → Звук и видео»: выбор микрофона, динамиков и камеры, проверка микрофона (шкала уровня), звука и камеры, обработка звука, состояние устройств и понятная диагностика
// ошибок с переходом в системные настройки Windows. Устройства запоминаются по названию (см. common/media.ts) и применяются страницами организаций как значения по умолчанию.
// Проверка не меняет системных параметров, не требует прав администратора и не занимает микрофон «монопольно»: работает общий режим Windows, встреча не прерывается.
import { deviceKind, diagnoseMic, diffDevices, EXCLUSIVE_STEPS, findByLabel, kindAdvice, KIND_TEXT, levelOf, REBOOT_HINT, type DevInfo, type MediaPrefs, type MicProblem, type NoiseMode } from "../common/media";
import { call, h, icon } from "./ui";

type Kind = DevInfo["kind"];
type Os = { platform: string; mic: string; camera: string };
const NOISE_LABEL: [NoiseMode, string][] = [["off", "Выключено"], ["standard", "Стандартное"], ["enhanced", "Усиленное"]];
const NOISE_HINT: Record<NoiseMode, string> = {
  off: "Звук без обработки — для студийного микрофона.",
  standard: "Шумоподавление Chromium: вентиляторы, клавиатура, фоновый шум.",
  enhanced: "На странице встречи работает нейросеть RNNoise на вашем компьютере. Здесь, в проверке, применяется стандартное подавление.",
};
const OS_ACCESS: Record<string, string> = { granted: "разрешено", denied: "запрещено в параметрах конфиденциальности Windows", restricted: "ограничено политикой", "not-determined": "не запрашивалось", unknown: "нет данных" };
const TEST_LIMIT_MS = 60_000;                                // проверка микрофона и камеры выключается сама: устройство не остаётся занятым, если окно забыли

export interface AvHost {
  media(): MediaPrefs;
  save(patch: Partial<MediaPrefs>): void;
  platform(): string;
  inCall(): boolean;
}

export interface AvPanel { el: HTMLElement; stop(): void }

function toast(text: string): void {
  const t = h("div", { class: "toast", role: "status" }, text);
  document.body.append(t);
  setTimeout(() => t.remove(), 5000);
}

export function avPanel(host: AvHost): AvPanel {
  let devices: DevInfo[] = [];
  let withId: MediaDeviceInfo[] = [];
  let os: Os = { platform: host.platform(), mic: "unknown", camera: "unknown" };
  let micStream: MediaStream | null = null, camStream: MediaStream | null = null, ctx: AudioContext | null = null;
  let raf = 0, micTimer = 0, camTimer = 0, heardAt = 0, micStartedAt = 0, stopped = false;
  let lastSelectedMissing = false;

  const root = h("div", { class: "stack" });
  const problemBox = h("div", { class: "stack", "aria-live": "polite" });
  const meter = h("progress", { class: "bar", max: 100, value: 0, "aria-label": "Уровень сигнала микрофона" });
  const micState = h("span", { class: "small muted", role: "status" }, "Проверка не запущена");
  const micBtn = h("button", { class: "btn", type: "button" }, icon("mic"), "Проверить микрофон");
  const spkBtn = h("button", { class: "btn", type: "button" }, icon("volume"), "Проверить звук");
  const camBtn = h("button", { class: "btn", type: "button" }, icon("video"), "Включить просмотр");
  const video = h("video", { class: "cam-preview", autoplay: true, muted: true, playsInline: true, "aria-label": "Просмотр камеры" });
  video.hidden = true;
  const status = h("div", { class: "small muted", role: "status" });
  const selects: Record<"mic" | "speaker" | "camera", HTMLSelectElement> = {
    mic: h("select", { class: "input", id: "av-mic", "aria-label": "Микрофон" }), speaker: h("select", { class: "input", id: "av-speaker", "aria-label": "Устройство воспроизведения" }),
    camera: h("select", { class: "input", id: "av-camera", "aria-label": "Камера" }),
  };
  const notes: Record<"mic" | "speaker" | "camera", HTMLElement> = { mic: h("small", { class: "muted" }), speaker: h("small", { class: "muted" }), camera: h("small", { class: "muted" }) };
  const KIND: Record<"mic" | "speaker" | "camera", Kind> = { mic: "audioinput", speaker: "audiooutput", camera: "videoinput" };

  // ------------------------------------------------------------------------------------------ список устройств
  async function refresh(announce: boolean): Promise<void> {
    let list: MediaDeviceInfo[] = [];
    try { list = await navigator.mediaDevices.enumerateDevices(); } catch { list = []; }
    // «default» и «communications» — не самостоятельные устройства, а ссылки Chromium на системный выбор Windows; в списке их заменяет «По умолчанию в Windows»
    list = list.filter((d) => d.deviceId !== "default" && d.deviceId !== "communications");
    const next: DevInfo[] = list.map((d) => ({ kind: d.kind as Kind, label: d.label }));
    if (announce) { const d = diffDevices(devices, next); for (const m of [...d.added, ...d.removed]) toast(m); }
    devices = next;
    withId = list;
    try { os = (await call<Os>("shell:av:state")) ?? os; } catch { /* состояние разрешений необязательно */ }
    renderSelects();
    renderStatus();
  }

  function fill(sel: HTMLSelectElement, role: "mic" | "speaker" | "camera", wanted: string): void {
    const list = withId.filter((d) => d.kind === KIND[role]);
    const missing = !!wanted && !findByLabel(list, wanted);
    const opts = [h("option", { value: "" }, "По умолчанию в Windows")];
    list.forEach((d, i) => opts.push(h("option", { value: d.label || `#${i}` }, d.label || `${{ mic: "Микрофон", speaker: "Устройство воспроизведения", camera: "Камера" }[role]} ${i + 1}`)));
    if (missing) opts.push(h("option", { value: wanted }, `${wanted} — не подключено`));
    sel.replaceChildren(...opts);
    sel.value = wanted;
    if (role === "mic") lastSelectedMissing = missing;
    const dev = list.find((d) => d.label === wanted);
    const kind = dev ? deviceKind(dev.label) : "other";
    notes[role].textContent = missing ? "Выбранное устройство сейчас не подключено — в встрече будет использовано устройство по умолчанию."
      : [KIND_TEXT[kind], kindAdvice(kind, role)].filter(Boolean).join(". ");
  }

  function renderSelects(): void {
    const m = host.media();
    fill(selects.mic, "mic", m.mic);
    fill(selects.speaker, "speaker", m.speaker);
    fill(selects.camera, "camera", m.camera);
    selects.speaker.disabled = !("setSinkId" in HTMLMediaElement.prototype);
    micBtn.disabled = !withId.some((d) => d.kind === "audioinput");
    spkBtn.disabled = selects.speaker.disabled && !withId.some((d) => d.kind === "audiooutput");
    camBtn.disabled = !withId.some((d) => d.kind === "videoinput");
  }

  function renderStatus(): void {
    const n = (k: Kind) => devices.filter((d) => d.kind === k).length;
    const parts = [`микрофонов ${n("audioinput")}`, `устройств воспроизведения ${n("audiooutput")}`, `камер ${n("videoinput")}`];
    status.textContent = `Windows видит: ${parts.join(", ")}.` + (os.platform === "win32" ? ` Доступ приложений: микрофон — ${OS_ACCESS[os.mic] ?? os.mic}; камера — ${OS_ACCESS[os.camera] ?? os.camera}.` : "");
  }

  // ------------------------------------------------------------------------------------------ микрофон
  function stopMic(): void {
    cancelAnimationFrame(raf); clearTimeout(micTimer);
    micStream?.getTracks().forEach((t) => t.stop()); micStream = null;
    void ctx?.close().catch(() => undefined); ctx = null;
    meter.value = 0;
    micBtn.replaceChildren(icon("mic"), "Проверить микрофон");
  }

  function show(problem: MicProblem | null): void {
    if (!problem || problem.cause === "none") { problemBox.replaceChildren(); return; }
    const act: Record<string, () => void> = {
      open_mic_settings: () => void call("shell:sound:open", { kind: "mic-classic" }),
      open_sound_settings: () => void call("shell:sound:open", { kind: "sound" }),
      open_privacy_mic: () => void call("shell:sound:open", { kind: "privacy-mic" }),
      retry: () => void startMic(),
    };
    const LABEL: Record<string, string> = { open_mic_settings: "Открыть настройки микрофона Windows", open_sound_settings: "Параметры звука Windows", open_privacy_mic: "Параметры конфиденциальности микрофона", retry: "Повторить" };
    problemBox.replaceChildren(h("div", { class: `alert ${problem.confidence === "certain" ? "err" : "warn"}`, role: "alert" }, icon("info"), h("div", { class: "body" },
      h("b", null, problem.title), h("span", null, problem.text),
      problem.confidence === "possible" ? h("span", { class: "small" }, "Это наиболее вероятное объяснение, а не установленный факт.") : null,
      h("ol", { class: "how" }, problem.steps.map((s) => h("li", null, h("span", null, s)))),
      problem.reboot ? h("span", { class: "small" }, REBOOT_HINT) : null,
      h("div", { class: "actions", style: { justifyContent: "flex-start" } }, problem.actions.map((a) => h("button", { class: `btn sm${a === "retry" ? " primary" : ""}`, type: "button", on: { click: act[a] } }, LABEL[a]))))));
  }

  async function startMic(): Promise<void> {
    stopMic();
    show(null);
    const m = host.media();
    const dev = findByLabel(withId.filter((d) => d.kind === "audioinput"), m.mic);
    micState.textContent = "Открываем микрофон…";
    micBtn.disabled = true;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: { ...(dev ? { deviceId: { exact: dev.deviceId } } : {}), echoCancellation: m.echo, noiseSuppression: m.noise !== "off", autoGainControl: m.agc }, video: false });
    } catch (e) {
      micBtn.disabled = false;
      micState.textContent = "Микрофон не открылся";
      await refresh(false);
      show(diagnoseMic({ platform: os.platform, error: { name: (e as DOMException)?.name, message: (e as Error)?.message }, osAccess: os.mic, micCount: devices.filter((d) => d.kind === "audioinput").length, selectedMissing: lastSelectedMissing }));
      return;
    }
    micBtn.disabled = false;
    if (stopped) { stopMic(); return; }
    await refresh(false);                                                  // после первого открытия названия устройств точнее
    const track = micStream.getAudioTracks()[0];
    micBtn.replaceChildren(icon("stop"), "Остановить проверку");
    track.onended = () => { stopMic(); micState.textContent = "Микрофон отключился во время проверки"; void refresh(true); };
    track.onmute = () => { micState.textContent = "Микрофон выключен в системе (кнопкой на корпусе, клавишей ноутбука или в параметрах Windows)"; };
    track.onunmute = () => { heardAt = performance.now(); };
    ctx = new AudioContext({ sinkId: { type: "none" } } as AudioContextOptions);   // шкала уровня ничего не воспроизводит: устройство вывода не открываем
    const src = ctx.createMediaStreamSource(micStream);
    const an = ctx.createAnalyser();
    an.fftSize = 1024;
    src.connect(an);
    const buf = new Float32Array(an.fftSize);
    micStartedAt = heardAt = performance.now();
    let heard = false;
    const loop = (): void => {
      an.getFloatTimeDomainData(buf);
      const lv = levelOf(buf);
      meter.value = Math.round(lv * 100);
      const now = performance.now();
      if (lv > 0.25) { heard = true; heardAt = now; micState.textContent = "Сигнал есть — микрофон работает"; }
      else if (!heard && now - micStartedAt > 3000 && !track.muted) micState.textContent = "Сигнала нет. Скажите что-нибудь; если шкала не двигается, проверьте кнопку выключения на гарнитуре и уровень входа в Windows";
      else if (heard && now - heardAt > 4000) micState.textContent = "Сигнал был, сейчас тишина";
      raf = requestAnimationFrame(loop);
    };
    loop();
    micTimer = window.setTimeout(() => { stopMic(); micState.textContent = "Проверка остановлена автоматически через минуту"; }, TEST_LIMIT_MS);
    if (host.inCall()) micState.textContent += " (встреча при этом не прерывается)";
  }

  // ------------------------------------------------------------------------------------------ динамики
  async function playTone(): Promise<void> {
    spkBtn.disabled = true;
    const sinkLabel = host.media().speaker;
    const sink = findByLabel(withId.filter((d) => d.kind === "audiooutput"), sinkLabel);
    try {
      const ac = new AudioContext();
      const target = sink?.deviceId;
      let routed = true;
      if (target) {
        const c = ac as AudioContext & { setSinkId?: (id: string) => Promise<void> };
        if (typeof c.setSinkId === "function") await c.setSinkId(target);
        else routed = false;
      }
      const gain = ac.createGain();
      gain.gain.value = 0;
      gain.connect(ac.destination);
      const t0 = ac.currentTime + 0.05;
      [440, 554.37, 659.25].forEach((f, i) => {
        const o = ac.createOscillator();
        o.type = "sine"; o.frequency.value = f;
        o.connect(gain);
        const t = t0 + i * 0.3;
        gain.gain.setValueAtTime(0, t); gain.gain.linearRampToValueAtTime(0.18, t + 0.04); gain.gain.linearRampToValueAtTime(0, t + 0.26);
        o.start(t); o.stop(t + 0.28);
      });
      status.textContent = routed ? "Воспроизводится короткая мелодия. Если вы её не слышите, проверьте выбранное устройство и громкость в Windows." : "Выбрать устройство воспроизведения для проверки не удалось — звук идёт на устройство по умолчанию.";
      setTimeout(() => { void ac.close().catch(() => undefined); renderStatus(); }, 1400);
    } catch (e) {
      status.textContent = `Не удалось воспроизвести звук: ${(e as Error)?.message ?? e}. Выберите другое устройство воспроизведения.`;
    } finally {
      spkBtn.disabled = false;
    }
  }

  // ------------------------------------------------------------------------------------------ камера
  function stopCam(): void {
    clearTimeout(camTimer);
    camStream?.getTracks().forEach((t) => t.stop()); camStream = null;
    video.srcObject = null; video.hidden = true;
    camBtn.replaceChildren(icon("video"), "Включить просмотр");
  }
  async function startCam(): Promise<void> {
    if (camStream) { stopCam(); return; }
    const dev = findByLabel(withId.filter((d) => d.kind === "videoinput"), host.media().camera);
    camBtn.disabled = true;
    try {
      camStream = await navigator.mediaDevices.getUserMedia({ video: { ...(dev ? { deviceId: { exact: dev.deviceId } } : {}), width: { ideal: 640 }, height: { ideal: 360 } }, audio: false });
    } catch (e) {
      camBtn.disabled = false;
      const n = (e as DOMException)?.name;
      status.textContent = n === "NotAllowedError" ? `Доступ к камере не выдан${os.camera === "denied" ? ": в параметрах конфиденциальности Windows он выключен" : ""}.`
        : n === "NotFoundError" ? "Камера не найдена или отключена." : n === "NotReadableError" ? "Камеру не удалось открыть: её, вероятно, использует другая программа." : `Камера не открылась (${n || "ошибка"}).`;
      if (n === "NotAllowedError" && os.platform === "win32") problemBox.replaceChildren(h("button", { class: "btn sm", type: "button", on: { click: () => void call("shell:sound:open", { kind: "privacy-cam" }) } }, "Параметры конфиденциальности камеры"));
      return;
    }
    camBtn.disabled = false;
    if (stopped) { stopCam(); return; }
    video.srcObject = camStream; video.hidden = false;
    camBtn.replaceChildren(icon("videoOff"), "Выключить просмотр");
    camStream.getVideoTracks()[0].onended = () => { stopCam(); status.textContent = "Камера отключилась во время просмотра."; void refresh(true); };
    camTimer = window.setTimeout(() => { stopCam(); status.textContent = "Просмотр камеры остановлен автоматически через минуту."; }, TEST_LIMIT_MS);
    await refresh(false);
  }

  // ------------------------------------------------------------------------------------------ сборка панели
  const pick = (role: "mic" | "speaker" | "camera") => () => { host.save({ [role]: selects[role].value.startsWith("#") ? "" : selects[role].value }); void (role === "mic" && micStream ? startMic() : role === "camera" && camStream ? (stopCam(), startCam()) : undefined); };
  for (const r of ["mic", "speaker", "camera"] as const) selects[r].addEventListener("change", pick(r));
  micBtn.addEventListener("click", () => { if (micStream) { stopMic(); micState.textContent = "Проверка остановлена"; } else void startMic(); });
  spkBtn.addEventListener("click", () => void playTone());
  camBtn.addEventListener("click", () => void startCam());

  const m0 = host.media();
  const noise = h("div", { class: "seg", role: "radiogroup", "aria-label": "Шумоподавление" });
  const noiseHint = h("small", { class: "muted" }, NOISE_HINT[m0.noise]);
  for (const [v, label] of NOISE_LABEL) {
    const b = h("button", { type: "button", role: "radio", "aria-checked": String(v === m0.noise), "aria-pressed": String(v === m0.noise) }, label);
    b.addEventListener("click", () => {
      host.save({ noise: v }); noiseHint.textContent = NOISE_HINT[v];
      for (const x of noise.children) { x.setAttribute("aria-pressed", String(x === b)); x.setAttribute("aria-checked", String(x === b)); }
      if (micStream) void startMic();
    });
    noise.append(b);
  }
  const sw = (label: string, hint: string, checked: boolean, on: (v: boolean) => void): HTMLElement =>
    h("label", { class: "switch" }, h("span", { class: "txt" }, h("b", null, label), h("small", null, hint)),
      h("input", { type: "checkbox", role: "switch", checked, on: { change: (e: Event) => { on((e.target as HTMLInputElement).checked); if (micStream) void startMic(); } } }));

  const fieldOf = (title: string, role: "mic" | "speaker" | "camera", ...extra: (Node | null)[]) => h("div", { class: "field" }, h("span", null, title), selects[role], notes[role], ...extra);

  const winHelp = h("details", { class: "card flat" },
    h("summary", null, h("b", null, "Если микрофон занят или не работает: монопольный режим Windows")),
    h("ol", { class: "how" }, EXCLUSIVE_STEPS.map((s) => h("li", null, h("span", null, s)))),
    h("p", { class: "small muted" }, REBOOT_HINT));
  const sysRow = h("div", { class: "actions", style: { justifyContent: "flex-start" } },
    h("button", { class: "btn", type: "button", on: { click: () => void call("shell:sound:open", { kind: "mic-classic" }) } }, icon("gear"), "Открыть настройки микрофона Windows"),
    h("button", { class: "btn ghost", type: "button", on: { click: () => void call("shell:sound:open", { kind: "privacy-mic" }) } }, "Разрешения микрофона"));
  const winOnly = os.platform !== "win32";

  root.append(
    h("div", { class: "alert info" }, icon("info"), h("div", { class: "body" }, h("b", null, "Как это работает"),
      h("span", null, "Выбранные устройства подставляются при входе во встречу на серверах Peregovorka версии 0.40 и новее. Во время встречи устройство всегда можно сменить в самой встрече."))),
    h("div", { class: "card flat stack" },
      fieldOf("Микрофон", "mic", h("div", { class: "row" }, micBtn, micState), meter),
      fieldOf("Устройство воспроизведения", "speaker", h("div", { class: "row" }, spkBtn)),
      fieldOf("Камера", "camera", h("div", { class: "row" }, camBtn), video),
      status),
    problemBox,
    h("div", { class: "card flat stack" },
      h("div", { class: "field" }, h("span", null, "Шумоподавление микрофона"), noise, noiseHint),
      sw("Эхоподавление", "Нужно, если слушаете через колонки. В наушниках можно выключить.", m0.echo, (v) => host.save({ echo: v })),
      sw("Автоусиление", "Выравнивает громкость голоса.", m0.agc, (v) => host.save({ agc: v }))),
    h("div", { class: "card flat stack" }, h("h3", { style: { margin: 0 } }, "Настройки Windows"),
      h("p", { class: "small muted" }, "Откроется окно «Звук» (вкладка «Запись»): там выбирается микрофон по умолчанию, уровень и монопольный режим. Приложение ничего не меняет в Windows само."), sysRow, winHelp));
  if (winOnly) { sysRow.hidden = true; winHelp.hidden = true; }

  const onChange = () => void refresh(true);
  navigator.mediaDevices?.addEventListener?.("devicechange", onChange);
  void refresh(false);

  return {
    el: root,
    stop() {
      stopped = true;
      navigator.mediaDevices?.removeEventListener?.("devicechange", onChange);
      stopMic(); stopCam();
    },
  };
}
