/**
 * Клиентская диагностика комнаты: этапы входа с таймингами, события демонстрации экрана и статистика WebRTC.
 * Всё отправляется на backend (/client/events, /client/metrics) и видно администратору; содержимого разговоров и секретов нет.
 */
import type { Room as LkRoom } from "livekit-client";
import { Track } from "livekit-client";
import { api } from "./api";

// ---------------------------------------------------------------------------------- этапы входа
export type Stage = "prepare" | "server" | "media" | "ready";
export const STAGE_LABEL: Record<Stage, string> = {
  prepare: "Подготовка встречи",
  server: "Подключение к серверу звонков",
  media: "Установка медиасоединения",
  ready: "Подключено",
};
export const STAGES: Stage[] = ["prepare", "server", "media", "ready"];
/** После скольких секунд этап считается «затянувшимся» и пользователю показывается пояснение. */
export const SLOW_STAGE_SECONDS = 3;

export const STAGE_HINT: Record<Stage, string> = {
  prepare: "Сервер готовит встречу и выдаёт пропуск. Если это долго — возможна медленная сеть до сервера.",
  server: "Устанавливается соединение с сервером звонков. Долгое ожидание бывает, если сеть или прокси не пропускают WebSocket.",
  media: "Согласуется путь для звука и видео (ICE). Долгое ожидание бывает при закрытых UDP/TCP-портах медиа или сложной сети (VPN, прокси).",
  ready: "",
};

export type Mark = "click" | "joinStart" | "joinEnd" | "connectStart" | "signalConnected" | "mediaConnected" | "active" | "micStart" | "micPublished";

/** Хронология входа: метки времени (performance.now) и вычисление интервалов. */
export class JoinTimeline {
  private m: Partial<Record<Mark, number>> = {};
  mark(name: Mark, at = performance.now()): void { if (this.m[name] === undefined) this.m[name] = at; }
  reset(): void { this.m = {}; }
  private span(a: Mark, b: Mark): number | undefined {
    const x = this.m[a], y = this.m[b];
    return x === undefined || y === undefined ? undefined : Math.max(0, Math.round(y - x));
  }
  /** Метрики, совпадающие по именам с серверными (admin → «Состояние системы»). */
  metrics(): Record<string, number | undefined> {
    return {
      join_api_ms: this.span("joinStart", "joinEnd"),
      signaling_connect_ms: this.span("connectStart", "signalConnected"),
      ice_connect_ms: this.span("signalConnected", "mediaConnected"),
      participant_active_ms: this.span("click", "active"),
      microphone_publish_ms: this.span("micStart", "micPublished"),
    };
  }
  /** Время, прошедшее на текущем этапе (для подсказки «дольше обычного»). */
  stageMs(stage: Stage, now = performance.now()): number {
    const start: Record<Stage, Mark> = { prepare: "joinStart", server: "connectStart", media: "signalConnected", ready: "active" };
    const t = this.m[start[stage]];
    return t === undefined ? 0 : Math.max(0, now - t);
  }
}

export function reportEvent(event: string, fields: { meetingId?: string; reason?: string; detail?: string } = {}): void {
  api.clientEvent({ event, meeting_id: fields.meetingId, reason: fields.reason, detail: fields.detail?.slice(0, 280) });
}

// ------------------------------------------------------------------------------- статистика WebRTC
export interface ScreenStats {
  fps?: number; bitrateKbps?: number; width?: number; height?: number; packetsLost?: number; framesDropped?: number;
  jitterMs?: number; rttMs?: number; limitReason?: string;
}
export interface Snapshot {
  at: number; rttMs?: number; lossPct?: number; outKbps?: number; inKbps?: number; candidate?: string;
  screenOut?: ScreenStats; screenIn?: ScreenStats;
}

type AnyStats = Record<string, unknown>;
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const sum = (a: (number | undefined)[]) => a.reduce<number>((s, x) => s + (x ?? 0), 0);
const max = (a: (number | undefined)[]) => { const v = a.filter((x): x is number => x !== undefined); return v.length ? Math.max(...v) : undefined; };

/** Считает битрейт по приращению счётчика байт между вызовами. */
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

async function selectedCandidate(room: LkRoom): Promise<{ text?: string; rttMs?: number }> {
  const lp = room.localParticipant;
  const tracks = [Track.Source.ScreenShare, Track.Source.Microphone, Track.Source.Camera]
    .map((s) => lp.getTrackPublication(s)?.track).filter(Boolean) as { sender?: RTCRtpSender }[];
  for (const t of tracks) {
    const sender = t.sender;
    if (!sender) continue;
    try {
      const pair = sender.transport?.iceTransport?.getSelectedCandidatePair?.();
      let rttMs: number | undefined;
      const rep = await sender.getStats();
      rep.forEach((r: AnyStats) => {
        if (r.type === "candidate-pair" && (r.nominated || r.selected) && num(r.currentRoundTripTime) !== undefined) rttMs = Math.round((r.currentRoundTripTime as number) * 1000);
      });
      const l = pair?.local;
      return { text: l ? `${l.protocol ?? "?"}/${l.type ?? "?"}` : undefined, rttMs };
    } catch { /* браузер не даёт — идём дальше */ }
  }
  return {};
}

/** Один замер: RTT, потери, битрейт, выбранный ICE-кандидат и подробности по трансляции экрана (исходящей и входящей). */
export async function sampleRoom(room: LkRoom, meter: RateMeter): Promise<Snapshot> {
  const now = performance.now();
  const snap: Snapshot = { at: Date.now() };
  const lp = room.localParticipant;
  try {
    const outBytes: (number | undefined)[] = [];
    let lost = 0, sent = 0;
    const rtts: (number | undefined)[] = [];
    for (const src of [Track.Source.Microphone, Track.Source.Camera, Track.Source.ScreenShare]) {
      const tr = lp.getTrackPublication(src)?.track as unknown as { getSenderStats?: () => Promise<AnyStats[] | AnyStats | undefined> } | undefined;
      if (!tr?.getSenderStats) continue;
      const raw = await tr.getSenderStats();
      const layers = (Array.isArray(raw) ? raw : raw ? [raw] : []) as AnyStats[];
      outBytes.push(sum(layers.map((l) => num(l.bytesSent))));
      lost += sum(layers.map((l) => num(l.packetsLost))); sent += sum(layers.map((l) => num(l.packetsSent)));
      rtts.push(max(layers.map((l) => (num(l.roundTripTime) !== undefined ? (l.roundTripTime as number) * 1000 : undefined))));
      if (src === Track.Source.ScreenShare && layers.length) {
        const top = layers.reduce((a, b) => ((num(b.frameWidth) ?? 0) > (num(a.frameWidth) ?? 0) ? b : a));
        const limit = layers.map((l) => l.qualityLimitationReason).find((r) => typeof r === "string" && r !== "none") as string | undefined;
        snap.screenOut = {
          fps: max(layers.map((l) => num(l.framesPerSecond))), width: num(top.frameWidth), height: num(top.frameHeight),
          bitrateKbps: meter.kbps("screen-out", sum(layers.map((l) => num(l.bytesSent))), now),
          packetsLost: sum(layers.map((l) => num(l.packetsLost))), jitterMs: max(layers.map((l) => (num(l.jitter) !== undefined ? (l.jitter as number) * 1000 : undefined))),
          rttMs: max(layers.map((l) => (num(l.roundTripTime) !== undefined ? (l.roundTripTime as number) * 1000 : undefined))), limitReason: limit,
        };
      }
    }
    snap.outKbps = meter.kbps("out", sum(outBytes), now);
    snap.lossPct = sent > 0 ? Math.round((lost / (lost + sent)) * 1000) / 10 : undefined;
    snap.rttMs = max(rtts);

    let inBytes = 0, haveIn = false;
    for (const p of room.remoteParticipants.values()) {
      for (const pub of p.trackPublications.values()) {
        const tr = pub.track as unknown as { getReceiverStats?: () => Promise<AnyStats | undefined> } | undefined;
        if (!tr?.getReceiverStats) continue;
        const s = await tr.getReceiverStats();
        if (!s) continue;
        haveIn = true; inBytes += num(s.bytesReceived) ?? 0;
        if (pub.source === Track.Source.ScreenShare) {
          snap.screenIn = {
            fps: num(s.framesPerSecond), width: num(s.frameWidth), height: num(s.frameHeight),
            bitrateKbps: meter.kbps("screen-in", num(s.bytesReceived), now), packetsLost: num(s.packetsLost), framesDropped: num(s.framesDropped),
            jitterMs: num(s.jitter) !== undefined ? (s.jitter as number) * 1000 : undefined,
          };
        }
      }
    }
    snap.inKbps = haveIn ? meter.kbps("in", inBytes, now) : undefined;
  } catch { /* статистика необязательна */ }
  const c = await selectedCandidate(room);
  snap.candidate = c.text;
  if (snap.rttMs === undefined) snap.rttMs = c.rttMs;
  return snap;
}

/** Тело для /client/metrics: только числа и короткие метки. */
export function metricsBody(meetingId: string, snap: Snapshot | null, join?: Record<string, number | undefined>): Record<string, unknown> {
  const s = snap?.screenOut ?? snap?.screenIn;
  return {
    meeting_id: meetingId, ...join,
    rtt_ms: snap?.rttMs, packet_loss_pct: snap?.lossPct, bitrate_out_kbps: snap?.outKbps, bitrate_in_kbps: snap?.inKbps, candidate: snap?.candidate,
    screen: s ? { fps: s.fps, bitrate_kbps: s.bitrateKbps, width: s.width, height: s.height, limit_reason: s.limitReason, frames_dropped: s.framesDropped } : undefined,
  };
}
