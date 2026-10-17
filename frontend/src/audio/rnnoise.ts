import type { AudioProcessorOptions, Track, TrackProcessor } from "livekit-client";

/**
 * «Усиленное» шумоподавление: RNNoise (нейросеть xiph/rnnoise в WebAssembly) в AudioWorklet на устройстве участника — звук не уходит на сторонние серверы.
 * RNNoise работает на 48 кГц, поэтому у процессора свой AudioContext. Всё подгружается лениво: основной пакет страницы не растёт, а ошибка загрузки
 * (старый браузер, запрет WebAssembly политикой CSP, нехватка ресурсов) превращается в исключение `init`, по которому вызывающий код откатывается на «стандартный» режим.
 */
const SAMPLE_RATE = 48000;

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
    try { this.source?.disconnect(); this.node?.disconnect(); this.node?.destroy(); this.dest?.disconnect(); } catch { /* уже отключено */ }
    this.processedTrack?.stop();
    await this.ctx?.close().catch(() => undefined);
    this.ctx = this.source = this.node = this.dest = this.processedTrack = undefined;
  }

  private async build(): Promise<void> {
    const [{ RnnoiseWorkletNode, loadRnnoise }, worklet, wasm, simd] = await Promise.all([
      import("@sapphi-red/web-noise-suppressor"),
      import("@sapphi-red/web-noise-suppressor/rnnoiseWorklet.js?url"),
      import("@sapphi-red/web-noise-suppressor/rnnoise.wasm?url"),
      import("@sapphi-red/web-noise-suppressor/rnnoise_simd.wasm?url"),
    ]);
    const ctx = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: "interactive" });
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
    this.node!.connect(this.dest);
    this.processedTrack = this.dest.stream.getAudioTracks()[0];
  }

  private attach(track: MediaStreamTrack): void {
    if (!this.ctx || !this.node) throw new Error("RNNoise не инициализирован");
    this.source = this.ctx.createMediaStreamSource(new MediaStream([track]));
    this.source.connect(this.node);
    void this.ctx.resume();
  }
}
