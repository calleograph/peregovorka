/**
 * Разбор WebRTC-статистики (RTCStatsReport) в плоские метрики для панели диагностики и сервера.
 * Чистые функции: принимают итерируемый набор отчётов, поэтому полностью тестируются без браузера.
 *
 * Принцип из Jitsi (lib-jitsi-meet, RTPStatsCollector/TrackStreamingStatus): «замёрзшая» картинка определяется не «на глаз», а по
 * счётчику framesDecoded, который перестал расти, пока поток считается активным — см. FreezeDetector.
 */

export interface MediaStats {
  codec?: string; width?: number; height?: number; fps?: number; bitrateKbps?: number;
  packetsSent?: number; packetsReceived?: number; packetsLost?: number; jitterMs?: number; rttMs?: number;
  nack?: number; pli?: number; fir?: number; framesEncoded?: number; framesDecoded?: number; framesDropped?: number;
  limitReason?: string; bytes?: number;
}
export interface PathStats { candidate?: string; localType?: string; remoteType?: string; protocol?: string; rttMs?: number }

type Rep = Record<string, unknown>;
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const ms = (v: unknown): number | undefined => (num(v) === undefined ? undefined : Math.round((v as number) * 1000 * 10) / 10);
const entries = (r: Iterable<Rep> | { forEach: (cb: (v: Rep) => void) => void }): Rep[] => {
  const out: Rep[] = [];
  if (typeof (r as { forEach?: unknown }).forEach === "function") (r as { forEach: (cb: (v: Rep) => void) => void }).forEach((v) => out.push(v));
  else for (const v of r as Iterable<Rep>) out.push(v);
  return out;
};

/** Исходящий видеопоток: все simulcast-слои суммируются, разрешение берётся с верхнего слоя. */
export function parseOutbound(report: Parameters<typeof entries>[0]): MediaStats {
  const all = entries(report);
  const codecs = new Map(all.filter((r) => r.type === "codec").map((r) => [r.id as string, String(r.mimeType ?? "").replace(/^(video|audio)\//, "")]));
  const outs = all.filter((r) => r.type === "outbound-rtp" && (r.kind ?? r.mediaType) === "video" && (num(r.bytesSent) ?? 0) >= 0);
  const remote = all.filter((r) => r.type === "remote-inbound-rtp");
  if (!outs.length) return {};
  const top = outs.reduce((a, b) => ((num(b.frameWidth) ?? 0) > (num(a.frameWidth) ?? 0) ? b : a));
  const sum = (k: string) => outs.reduce((s, r) => s + (num(r[k]) ?? 0), 0);
  const lim = outs.map((r) => r.qualityLimitationReason).find((x) => typeof x === "string" && x !== "none") as string | undefined;
  return {
    codec: codecs.get(top.codecId as string), width: num(top.frameWidth), height: num(top.frameHeight),
    fps: outs.reduce<number | undefined>((m, r) => (num(r.framesPerSecond) !== undefined ? Math.max(m ?? 0, r.framesPerSecond as number) : m), undefined),
    packetsSent: sum("packetsSent"), nack: sum("nackCount"), pli: sum("pliCount"), fir: sum("firCount"), framesEncoded: sum("framesEncoded"), bytes: sum("bytesSent"),
    packetsLost: remote.reduce((s, r) => s + (num(r.packetsLost) ?? 0), 0),
    jitterMs: remote.reduce<number | undefined>((m, r) => (ms(r.jitter) !== undefined ? Math.max(m ?? 0, ms(r.jitter)!) : m), undefined),
    rttMs: remote.reduce<number | undefined>((m, r) => (ms(r.roundTripTime) !== undefined ? Math.max(m ?? 0, ms(r.roundTripTime)!) : m), undefined),
    limitReason: lim,
  };
}

/** Входящий видеопоток (то, что получаем от других). */
export function parseInbound(report: Parameters<typeof entries>[0]): MediaStats {
  const all = entries(report);
  const codecs = new Map(all.filter((r) => r.type === "codec").map((r) => [r.id as string, String(r.mimeType ?? "").replace(/^(video|audio)\//, "")]));
  const inn = all.find((r) => r.type === "inbound-rtp" && (r.kind ?? r.mediaType) === "video");
  if (!inn) return {};
  return {
    codec: codecs.get(inn.codecId as string), width: num(inn.frameWidth), height: num(inn.frameHeight), fps: num(inn.framesPerSecond),
    packetsReceived: num(inn.packetsReceived), packetsLost: num(inn.packetsLost), jitterMs: ms(inn.jitter), nack: num(inn.nackCount), pli: num(inn.pliCount),
    fir: num(inn.firCount), framesDecoded: num(inn.framesDecoded), framesDropped: num(inn.framesDropped), bytes: num(inn.bytesReceived),
  };
}

/** Выбранная пара ICE-кандидатов: тип пути (host/srflx/relay), протокол, RTT. */
export function parsePath(report: Parameters<typeof entries>[0]): PathStats {
  const all = entries(report);
  const byId = new Map(all.map((r) => [r.id as string, r]));
  const transport = all.find((r) => r.type === "transport" && r.selectedCandidatePairId);
  const pair = (transport && byId.get(transport.selectedCandidatePairId as string))
    ?? all.find((r) => r.type === "candidate-pair" && (r.nominated || r.selected) && r.state !== "failed");
  if (!pair) return {};
  const l = byId.get(pair.localCandidateId as string), r = byId.get(pair.remoteCandidateId as string);
  const lt = l?.candidateType as string | undefined, rt = r?.candidateType as string | undefined, pr = (l?.protocol ?? r?.protocol) as string | undefined;
  return { localType: lt, remoteType: rt, protocol: pr, candidate: lt || pr ? `${pr ?? "?"}/${lt ?? "?"}→${rt ?? "?"}` : undefined, rttMs: ms(pair.currentRoundTripTime) };
}

/** Битрейт по приращению счётчика байт между вызовами (ключ — поток). */
export class RateMeter {
  private last = new Map<string, { bytes: number; t: number }>();
  kbps(key: string, bytes: number | undefined, t: number): number | undefined {
    if (bytes === undefined) return undefined;
    const p = this.last.get(key);
    this.last.set(key, { bytes, t });
    if (!p || t <= p.t || bytes < p.bytes) return undefined;
    return Math.round(((bytes - p.bytes) * 8) / (t - p.t));
  }
}

/**
 * «Заморозка» входящего видео: счётчик декодированных кадров не растёт `needed` замеров подряд, хотя байты приходят или поток
 * заявлен активным. Возвращает true один раз при наступлении заморозки и ещё раз (false→) при возобновлении через `recovered`.
 */
export class FreezeDetector {
  private last = new Map<string, { frames: number; stale: number; frozen: boolean }>();
  constructor(private needed = 3) {}
  update(key: string, framesDecoded: number | undefined): "frozen" | "recovered" | null {
    if (framesDecoded === undefined) return null;
    const p = this.last.get(key);
    if (!p) { this.last.set(key, { frames: framesDecoded, stale: 0, frozen: false }); return null; }
    if (framesDecoded > p.frames) {
      const was = p.frozen;
      this.last.set(key, { frames: framesDecoded, stale: 0, frozen: false });
      return was ? "recovered" : null;
    }
    p.stale += 1;
    if (!p.frozen && p.stale >= this.needed) { p.frozen = true; return "frozen"; }
    return null;
  }
  reset(key: string): void { this.last.delete(key); }
}
