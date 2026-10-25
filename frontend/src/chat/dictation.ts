// Голосовая диктовка в поле сообщения: микрофон → PCM 16 кГц (worklet) → /api/v1/ws/dictation → распознанные фрагменты (ASR сервера: тот же, что у встреч).
// Микрофон освобождается сразу после остановки, отмены, закрытия чата и при смене или отключении устройства. Сама ничего не отправляет:
// «Остановить и отправить» решает вызывающий, только после окончательного ответа сервера («done»).

export type DictationPhase = "idle" | "starting" | "listening" | "finishing" | "error";
export interface DictationHandlers {
  onFinal: (seq: number, text: string) => void;
  onPhase: (p: DictationPhase, message?: string) => void;
  onLevel?: (v: number) => void;
}

const WORKLET = "/audio/pcm16.worklet.js";

export class Dictation {
  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private done: ((ok: boolean) => void) | null = null;
  private finished = false;
  private finals = 0;                 // сколько фрагментов текста пришло
  private peak = 0;                   // наибольший уровень звука за диктовку (0…1)
  phase: DictationPhase = "idle";

  constructor(private h: DictationHandlers) {}

  private setPhase(p: DictationPhase, message?: string) { this.phase = p; this.h.onPhase(p, message); }

  async start(deviceId?: string): Promise<void> {
    if (this.phase === "starting" || this.phase === "listening") return;
    this.finished = false; this.finals = 0; this.peak = 0;
    this.setPhase("starting");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { ...(deviceId ? { deviceId: { exact: deviceId } } : {}), channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      const name = (e as DOMException)?.name;
      this.fail(name === "NotAllowedError" ? "Нет доступа к микрофону: разрешите его в настройках браузера" : name === "NotFoundError" ? "Микрофон не найден" : "Не удалось включить микрофон");
      return;
    }
    const track = this.stream.getAudioTracks()[0];
    track?.addEventListener("ended", () => { if (this.phase === "listening") void this.stop(); });       // устройство отключили или сменили
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/v1/ws/dictation`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (e) => this.onMessage(e.data);
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.releaseMic();
      if (!this.finished) { this.finished = true; this.done?.(false); if (this.phase !== "error") this.setPhase("idle"); }
    };
    ws.onerror = () => { if (this.phase === "starting") this.fail("Нет связи с сервером распознавания"); };
  }

  private async begin(): Promise<void> {
    try {
      this.ctx = new AudioContext({ sampleRate: 16000, latencyHint: "interactive" });
      if (this.ctx.state === "suspended") await this.ctx.resume();            // без этого звук не идёт, если окружение не считает запуск действием пользователя
      await this.ctx.audioWorklet.addModule(WORKLET);
      const src = this.ctx.createMediaStreamSource(this.stream!);
      this.node = new AudioWorkletNode(this.ctx, "pg-pcm16", { numberOfInputs: 1, numberOfOutputs: 0 });
      this.node.port.onmessage = (e) => {
        const { pcm, level } = e.data as { pcm: ArrayBuffer; level: number };
        this.h.onLevel?.(level);
        if (level > this.peak) this.peak = level;
        if (this.ws?.readyState === WebSocket.OPEN && this.phase === "listening") this.ws.send(pcm);
      };
      src.connect(this.node);
      this.setPhase("listening");
    } catch {
      this.fail("Браузер не смог обработать звук микрофона");
    }
  }

  private onMessage(raw: unknown) {
    let m: Record<string, unknown>;
    try { m = JSON.parse(String(raw)); } catch { return; }
    switch (m.type) {
      case "ready": void this.begin(); break;
      case "final": if (String(m.text ?? "").trim()) this.finals++; this.h.onFinal(Number(m.seq), String(m.text ?? "")); break;
      case "busy": this.h.onPhase(this.phase, String(m.message ?? "Распознавание занято — часть фразы пропущена")); break;
      case "limit": void this.stop(); break;
      case "done": this.finish(true); break;
      case "error": this.fail(String(m.message ?? "Сбой распознавания")); break;
    }
  }

  private finish(ok: boolean) {
    if (this.finished) return;
    this.finished = true;
    this.releaseMic();
    try { this.ws?.close(1000); } catch { /* уже закрыт */ }
    this.ws = null;
    // сервер закончил, а текста нет, хотя звук был: человек должен это видеть, а не гадать, куда делась речь
    this.setPhase("idle", ok && this.finals === 0 && this.peak > 0.02 ? "Речь не распознана: проверьте микрофон и повторите. Если повторяется — сообщите администратору." : undefined);
    this.done?.(ok);
  }

  private fail(message: string) {
    this.releaseMic();
    const was = this.finished;
    this.finished = true;
    try { this.ws?.close(1000); } catch { /* уже закрыт */ }
    this.ws = null;
    this.setPhase("error", message);
    if (!was) this.done?.(false);
  }

  /** Микрофон действительно пропал: дорожка закончилась или её устройства нет в списке. Событие `devicechange` само ни о чём не говорит: в Windows оно приходит и при открытии
   *  микрофона, и при смене устройства по умолчанию, поэтому останавливать диктовку по нему одному нельзя. */
  async micLost(): Promise<boolean> {
    const t = this.stream?.getAudioTracks()[0];
    if (!t || t.readyState !== "live") return true;
    const id = t.getSettings().deviceId;
    if (!id) return false;
    try { return !(await navigator.mediaDevices.enumerateDevices()).some((d) => d.kind === "audioinput" && d.deviceId === id); } catch { return false; }
  }

  /** Остановить и дождаться окончательного текста (последняя фраза дорезается сервером). true — сервер подтвердил окончание. */
  stop(): Promise<boolean> {
    if (this.phase !== "listening" && this.phase !== "starting") return Promise.resolve(this.finished);
    this.releaseMic();                                  // микрофон — сразу, текст ещё дойдёт
    this.setPhase("finishing");
    const p = new Promise<boolean>((resolve) => { this.done = resolve; });
    try { this.ws?.send(JSON.stringify({ type: "stop" })); } catch { this.finish(false); }
    window.setTimeout(() => { if (!this.finished) this.fail("Распознавание не ответило вовремя — уже распознанный текст в поле ввода"); }, 40000);
    return p;
  }

  /** Отменить: распознанное до этого остаётся в поле (решает вызывающий), новые фрагменты не придут. */
  cancel(): void {
    try { this.ws?.send(JSON.stringify({ type: "cancel" })); } catch { /* закрыт */ }
    this.finished = true;
    this.releaseMic();
    try { this.ws?.close(1000); } catch { /* закрыт */ }
    this.ws = null;
    this.setPhase("idle");
    this.done?.(false);
  }

  private releaseMic() {
    try { this.node?.port.close(); this.node?.disconnect(); } catch { /* уже */ }
    this.node = null;
    for (const t of this.stream?.getTracks() ?? []) t.stop();
    this.stream = null;
    void this.ctx?.close().catch(() => undefined);
    this.ctx = null;
  }
}
