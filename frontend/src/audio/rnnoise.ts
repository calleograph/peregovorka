import type { AudioProcessorOptions, Track, TrackProcessor } from "livekit-client";

/**
 * «Усиленное» шумоподавление: RNNoise (нейросеть xiph/rnnoise в WebAssembly) в AudioWorklet на устройстве участника — звук не уходит на сторонние серверы.
 * RNNoise работает на 48 кГц, поэтому у процессора свой AudioContext. Всё подгружается лениво: основной пакет страницы не растёт, а ошибка загрузки
 * (старый браузер, запрет WebAssembly политикой CSP, нехватка ресурсов) превращается в исключение `init`, по которому вызывающий код откатывается на «стандартный» режим.
 */
const SAMPLE_RATE = 48000;
/** Затвор после RNNoise (public/audio/noise-gate.worklet.js): порог привязан к уровню самой речи, чтобы не срезать тихие слова; фиксированная компенсация громкости, которую забирает RNNoise. */
export const GATE_OPTIONS = { depthDb: 24, floorDb: -64, reduceDb: 14, holdMs: 200, lookaheadMs: 24, debounceMs: 18, attackMs: 2.5, releaseMs: 90, makeupDb: 0 };      // уровень речи на выходе измерен: цепочка RNNoise → затвор речь не ослабляет (tests/live/noise_probe.py), компенсация не нужна
const GATE_URL = "/audio/noise-gate.worklet.js";
const FALLBACK_GAIN_DB = 5;          // без затвора RNNoise напрямую в MediaStreamDestination отдаёт речь тише исходной примерно на 5 дБ (замер)

export function rnnoiseSupported(): boolean {
  return typeof AudioContext !== "undefined" && typeof AudioWorkletNode !== "undefined" && typeof WebAssembly !== "undefined";
}

export class RnnoiseProcessor implements TrackProcessor<Track.Kind.Audio, AudioProcessorOptions> {
  name = "rnnoise";
  processedTrack?: MediaStreamTrack;
  /** Вызывается, если обработчик в потоке звука упал уже после запуска: тогда звук пропадёт, нужен откат. */
  onError?: (reason: string) => void;
  private ctx?: AudioContext;
  private source?: MediaStreamAudioSourceNode;
  private node?: { destroy(): void } & AudioWorkletNode;
  private dest?: MediaStreamAudioDestinationNode;
  private tail?: AudioNode;            // затвор (или простая компенсация громкости, если затвор не загрузился)
  /** Как дошёл звук: с затвором или только RNNoise с компенсацией громкости (для диагностики). */
  stage: "gate" | "gain" | "none" = "none";

  async init(opts: AudioProcessorOptions): Promise<void> {
    if (!rnnoiseSupported()) throw new Error("AudioWorklet/WebAssembly недоступны");
    if (!this.ctx) await this.build();
    this.attach(opts.track);
  }

  async restart(opts: AudioProcessorOptions): Promise<void> {
    this.source?.disconnect();
    if (!this.ctx) await this.build();
    this.attach(opts.track);
  }

  async destroy(): Promise<void> {
    try { this.source?.disconnect(); this.node?.disconnect(); this.tail?.disconnect(); this.node?.destroy(); this.dest?.disconnect(); } catch { /* уже отключено */ }
    this.processedTrack?.stop();
    await this.ctx?.close().catch(() => undefined);
    this.ctx = this.source = this.node = this.dest = this.tail = this.processedTrack = undefined; this.stage = "none";
  }

  private async build(): Promise<void> {
    const [{ RnnoiseWorkletNode, loadRnnoise }, worklet, wasm, simd] = await Promise.all([
      import("@sapphi-red/web-noise-suppressor"),
      import("@sapphi-red/web-noise-suppressor/rnnoiseWorklet.js?url"),
      import("@sapphi-red/web-noise-suppressor/rnnoise.wasm?url"),
      import("@sapphi-red/web-noise-suppressor/rnnoise_simd.wasm?url"),
    ]);
    // Звук из этого контекста никуда не воспроизводится (выход идёт в MediaStreamDestination), поэтому устройство вывода ему не нужно: без sinkId "none" Chromium открывает
    // поток вывода на устройство Windows по умолчанию — лишний захват устройства (Bluetooth-гарнитура, занятый другой программой выход могут остановить обработку микрофона).
    // Браузеры без поддержки поля его игнорируют — поведение прежнее.
    const ctx = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: "interactive", sinkId: { type: "none" } } as AudioContextOptions);
    try {
      const wasmBinary = await loadRnnoise({ url: wasm.default, simdUrl: simd.default });
      await ctx.audioWorklet.addModule(worklet.default);
      const node = new RnnoiseWorkletNode(ctx, { maxChannels: 1, wasmBinary });
      node.onprocessorerror = () => this.onError?.("processorerror");
      this.node = node;
    } catch (e) {
      await ctx.close().catch(() => undefined);
      throw e;
    }
    this.ctx = ctx;
    this.dest = ctx.createMediaStreamDestination();
    try {
      await ctx.audioWorklet.addModule(GATE_URL);
      const gate = new AudioWorkletNode(ctx, "pg-noise-gate", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], processorOptions: GATE_OPTIONS });
      gate.onprocessorerror = () => this.onError?.("gate_processorerror");
      this.node!.connect(gate); gate.connect(this.dest); this.tail = gate; this.stage = "gate";
    } catch {                                     // затвор не загрузился (политика сайта, сеть): остаётся RNNoise с компенсацией громкости — шумоподавление работает, разговор не страдает
      const g = ctx.createGain(); g.gain.value = Math.pow(10, FALLBACK_GAIN_DB / 20);
      this.node!.connect(g); g.connect(this.dest); this.tail = g; this.stage = "gain";
    }
    this.processedTrack = this.dest.stream.getAudioTracks()[0];
  }

  private attach(track: MediaStreamTrack): void {
    if (!this.ctx || !this.node) throw new Error("RNNoise не инициализирован");
    this.source = this.ctx.createMediaStreamSource(new MediaStream([track]));
    this.source.connect(this.node);
    void this.ctx.resume();
  }
}
