/**
 * Клиентская диагностика комнаты: хронология входа с замерами (performance.mark/measure), события жизненного цикла Room и показа экрана
 * и статистика WebRTC. Отправляется на backend (/client/events, /client/metrics) и видна администратору; содержимого разговоров и
 * секретов нет.
 */
import type { Room as LkRoom } from "livekit-client";
import { Track } from "livekit-client";
import { api } from "./api";
import { redactSecrets } from "./lkDiag";
import { FreezeDetector, RateMeter, parseInbound, parseOutbound, parsePath, type MediaStats, type PathStats } from "./rtcStats";

export { FreezeDetector, RateMeter } from "./rtcStats";
export type { MediaStats, PathStats } from "./rtcStats";

// ---------------------------------------------------------------------------------- этапы входа
export type Stage = "prepare" | "server" | "media" | "ready";
export const STAGE_LABEL: Record<Stage, string> = {
  prepare: "Подготовка встречи",
  server: "Сигнал: соединение WebSocket с сервером звонков",
  media: "ICE: путь для звука и видео",
  ready: "Медиа: подключено",
};
export const STAGES: Stage[] = ["prepare", "server", "media", "ready"];
/** После скольких секунд этап считается «затянувшимся» и пользователю показывается пояснение. */
export const SLOW_STAGE_SECONDS = 3;

export const STAGE_HINT: Record<Stage, string> = {
  prepare: "Сервер готовит встречу и выдаёт пропуск. Если это долго — возможна медленная сеть до сервера.",
  server: "Устанавливается соединение с сервером звонков. Долгое ожидание бывает, если сеть или прокси не пропускают WebSocket, либо сервер звонков устарел (запасной путь /rtc).",
  media: "Согласуется путь для звука и видео (ICE). Долгое ожидание бывает при закрытых UDP/TCP-портах медиа или сложной сети (VPN, прокси).",
  ready: "",
};

export type Mark =
  | "click" | "joinStart" | "joinEnd" | "roomCreated" | "connectStart" | "signalConnected" | "mediaConnected" | "active"
  | "gumStart" | "gumEnd" | "micStart" | "micPublished" | "wsStart" | "wsOpen";

/** Хронология входа: метки времени (performance.now + performance.mark) и вычисление интервалов. */
export class JoinTimeline {
  private m: Partial<Record<Mark, number>> = {};
  mark(name: Mark, at = performance.now()): void {
    if (this.m[name] !== undefined) return;
    this.m[name] = at;
    try { performance.mark(`pg:${name}`, { startTime: at }); } catch { /* окружение без User Timing */ }
  }
  reset(): void {
    this.m = {};
    try { performance.clearMarks(); } catch { /* ignore */ }
  }
  private span(a: Mark, b: Mark): number | undefined {
    const x = this.m[a], y = this.m[b];
    return x === undefined || y === undefined ? undefined : Math.max(0, Math.round(y - x));
  }
  /** Метрики, совпадающие по именам с серверными (админка → «Обзор» → «Технические показатели»). */
  metrics(): Record<string, number | undefined> {
    return {
      join_api_ms: this.span("joinStart", "joinEnd"),
      room_create_ms: this.span("joinEnd", "roomCreated"),
      livekit_connect_ms: this.span("connectStart", "mediaConnected"),
      signaling_connect_ms: this.span("connectStart", "signalConnected"),
      ice_connect_ms: this.span("signalConnected", "mediaConnected"),
      participant_active_ms: this.span("click", "active"),
      get_user_media_ms: this.span("gumStart", "gumEnd"),
      microphone_publish_ms: this.span("micStart", "micPublished"),
      backend_ws_connect_ms: this.span("wsStart", "wsOpen"),
      total_join_ms: this.span("click", "active"),
    };
  }
  /** Записать итог в User Timing (видно во вкладке Performance браузера). */
  finish(): void {
    try { performance.measure("pg:total_join", "pg:click", "pg:active"); } catch { /* метки могли не стоять */ }
  }
  stageMs(stage: Stage, now = performance.now()): number {
    const start: Record<Stage, Mark> = { prepare: "joinStart", server: "connectStart", media: "signalConnected", ready: "active" };
    const t = this.m[start[stage]];
    return t === undefined ? 0 : Math.max(0, now - t);
  }
}

/** Идентификатор объекта Room: по нему в журнале видно, нормальное ли это переподключение или создан НОВЫЙ объект. */
export const newInstanceId = (): string => {
  try { return crypto.randomUUID().slice(0, 8); } catch { return Math.random().toString(16).slice(2, 10); }
};

export type RoomPhase = "ROOM_CREATE" | "CONNECT_START" | "SIGNALING_CONNECTED" | "ICE_CONNECTED" | "CONNECT_OK" | "RECONNECTING" | "RECONNECTED" | "DISCONNECTED" | "ROOM_DISPOSE";
export type ScreenPhase = "SCREEN_CREATE" | "SCREEN_PUBLISH_START" | "SCREEN_PUBLISH_OK" | "SCREEN_TRACK_ENDED" | "SCREEN_UNPUBLISH" | "SCREEN_ERROR";

export interface EventFields { meetingId?: string; reason?: string; detail?: string; room?: string; data?: Record<string, unknown> }
export function reportEvent(event: string, fields: EventFields = {}): void {
  // токены доступа и join_request из текстов ошибок удаляются до отправки на сервер
  api.clientEvent({ event, meeting_id: fields.meetingId, reason: fields.reason && redactSecrets(fields.reason, 200), detail: fields.detail && redactSecrets(fields.detail, 280), room: fields.room, data: scrub(fields.data) });
}
/** Строковые значения данных события проходят через redactSecrets (на случай URL с токеном в тексте). */
function scrub(data?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!data) return data;
  const walk = (v: unknown, depth = 0): unknown => {
    if (typeof v === "string") return redactSecrets(v, 300);
    if (depth > 4 || v === null || typeof v !== "object") return v;
    if (Array.isArray(v)) return v.slice(0, 40).map((x) => walk(x, depth + 1));
    return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, walk(x, depth + 1)]));
  };
  return walk(data) as Record<string, unknown>;
}
export const reportRoomPhase = (phase: RoomPhase, instance: string, meetingId?: string, extra = ""): void =>
  reportEvent("room_lifecycle", { meetingId, reason: phase, detail: `instance=${instance}${extra ? ` ${extra}` : ""}` });
export const reportScreenPhase = (phase: ScreenPhase, instance: string, meetingId?: string, extra = ""): void =>
  reportEvent("screen_lifecycle", { meetingId, reason: phase, detail: `instance=${instance}${extra ? ` ${extra}` : ""}` });

// ------------------------------------------------------------------------------- статистика WebRTC
export interface Snapshot {
  at: number; rttMs?: number; lossPct?: number; outKbps?: number; inKbps?: number;
  path?: PathStats; camera?: MediaStats; screenOut?: MediaStats; screenIn?: MediaStats; screenFrozen?: boolean;
}

type Reportable = { getStats?: () => Promise<{ forEach: (cb: (v: Record<string, unknown>) => void) => void }> };
const stats = async (x: Reportable | undefined) => (x?.getStats ? x.getStats().catch(() => undefined) : undefined);

/** Один замер: RTT, потери, битрейт, путь ICE, камера и показ экрана (исходящие и входящие) с кодеком, FPS, NACK/PLI, кадрами. */
export async function sampleRoom(room: LkRoom, meter: RateMeter, freeze?: FreezeDetector): Promise<Snapshot> {
  const now = performance.now();
  const snap: Snapshot = { at: Date.now() };
  const lp = room.localParticipant;
  let outBytes = 0, lost = 0, sent = 0, rtt: number | undefined;
  try {
    for (const [src, key] of [[Track.Source.Camera, "camera"], [Track.Source.ScreenShare, "screenOut"]] as const) {
      const sender = (lp.getTrackPublication(src)?.track as unknown as { sender?: Reportable } | undefined)?.sender;
      const rep = await stats(sender);
      if (!rep) continue;
      const m = parseOutbound(rep as never);
      m.bitrateKbps = meter.kbps(key, m.bytes, now);
      snap[key] = m;
      outBytes += m.bytes ?? 0; lost += m.packetsLost ?? 0; sent += m.packetsSent ?? 0;
      if (m.rttMs !== undefined) rtt = Math.max(rtt ?? 0, m.rttMs);
      if (!snap.path?.candidate) snap.path = parsePath(rep as never);
    }
    const micSender = (lp.getTrackPublication(Track.Source.Microphone)?.track as unknown as { sender?: Reportable } | undefined)?.sender;
    const micRep = await stats(micSender);
    if (micRep) {
      const all: Record<string, unknown>[] = [];
      micRep.forEach((v) => all.push(v));
      const out = all.find((r) => r.type === "outbound-rtp" && (r.kind ?? r.mediaType) === "audio");
      const rem = all.find((r) => r.type === "remote-inbound-rtp");
      if (out) { outBytes += Number(out.bytesSent ?? 0); sent += Number(out.packetsSent ?? 0); }
      if (rem) {
        lost += Number(rem.packetsLost ?? 0);
        if (typeof rem.roundTripTime === "number") rtt = Math.max(rtt ?? 0, Math.round(rem.roundTripTime * 1000));
      }
      if (!snap.path?.candidate) snap.path = parsePath(micRep as never);
    }
    snap.outKbps = meter.kbps("out", outBytes, now);
    snap.lossPct = sent > 0 ? Math.round((lost / (lost + sent)) * 1000) / 10 : undefined;
    snap.rttMs = rtt ?? snap.path?.rttMs;

    let inBytes = 0, haveIn = false;
    for (const p of room.remoteParticipants.values()) {
      for (const pub of p.trackPublications.values()) {
        const receiver = (pub.track as unknown as { receiver?: Reportable } | undefined)?.receiver;
        if (!receiver) continue;
        const rep = await stats(receiver);
        if (!rep) continue;
        const all: Record<string, unknown>[] = [];
        rep.forEach((v) => all.push(v));
        const inn = all.find((r) => r.type === "inbound-rtp");
        if (inn) { inBytes += Number(inn.bytesReceived ?? 0); haveIn = true; }
        if (pub.source === Track.Source.ScreenShare) {
          const m = parseInbound(rep as never);
          m.bitrateKbps = meter.kbps("screenIn", m.bytes, now);
          snap.screenIn = m;
          const f = freeze?.update(`screen:${p.identity}`, m.framesDecoded);
          if (f === "frozen") snap.screenFrozen = true;
        }
      }
    }
    snap.inKbps = haveIn ? meter.kbps("in", inBytes, now) : undefined;
  } catch { /* статистика необязательна */ }
  if (!snap.path?.candidate) {
    try {
      for (const t of [Track.Source.Microphone, Track.Source.Camera, Track.Source.ScreenShare]) {
        const sender = (lp.getTrackPublication(t)?.track as unknown as { sender?: RTCRtpSender } | undefined)?.sender;
        const pair = sender?.transport?.iceTransport?.getSelectedCandidatePair?.();
        if (pair?.local) {
          snap.path = { localType: pair.local.type ?? undefined, remoteType: pair.remote?.type ?? undefined, protocol: pair.local.protocol ?? undefined,
            candidate: `${pair.local.protocol ?? "?"}/${pair.local.type ?? "?"}→${pair.remote?.type ?? "?"}` };
          break;
        }
      }
    } catch { /* браузер не даёт */ }
  }
  return snap;
}

/** Тело для /client/metrics: только числа и короткие метки. */
export function metricsBody(meetingId: string, snap: Snapshot | null, join?: Record<string, number | undefined>): Record<string, unknown> {
  const s = snap?.screenOut ?? snap?.screenIn;
  return {
    meeting_id: meetingId, ...join,
    rtt_ms: snap?.rttMs, packet_loss_pct: snap?.lossPct, bitrate_out_kbps: snap?.outKbps, bitrate_in_kbps: snap?.inKbps, candidate: snap?.path?.candidate,
    screen: s ? { fps: s.fps, bitrate_kbps: s.bitrateKbps, width: s.width, height: s.height, limit_reason: s.limitReason, frames_dropped: s.framesDropped } : undefined,
  };
}
