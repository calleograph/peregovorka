import { Track, type LocalVideoTrack, type Room } from "livekit-client";
import { AdaptiveController, applyLevelToEncodings, cameraAllowed, type ConnQuality, type Decision, type Sample, type VideoLevel } from "./adaptive";
import { type Base, ladderNote, ladderParams, ScreenLadder } from "./screenLadder";

export interface QualityState {
  level: VideoLevel;
  manual: boolean;
  conn: ConnQuality;
  limited?: string;
  lossPct?: number;
  rttMs?: number;
  /** Автоподстройка показа экрана: ступень (0 — как выбран профиль), включена ли, что показывается (для подсказки). */
  screenLevel?: number;
  screenAuto?: boolean;
  /** Камера выключена самим менеджером (уровень «Только звук»), а не пользователем: при улучшении её нужно вернуть. */
  cameraSuspended: boolean;
}

/** Доля потерянных пакетов за интервал между двумя замерами (счётчики накопительные); без данных — undefined. */
export function lossPercent(prev: { sent: number; lost: number } | undefined, cur: { sent: number; lost: number }): number | undefined {
  if (!prev) return undefined;
  const sent = cur.sent - prev.sent, lost = cur.lost - prev.lost;
  return sent > 0 && lost >= 0 ? Math.min(100, (lost / (sent + lost)) * 100) : undefined;
}

const INTERVAL_MS = 5000;

/** Следит за соединением, меняет уровень видео камеры и держит звук в приоритете. Все сбои замеров и применения глотаются: адаптация не должна рвать разговор. */
export class QualityManager {
  private ctl = new AdaptiveController("high");
  private timer: ReturnType<typeof setInterval> | undefined;
  private prev: { sent: number; lost: number } | undefined;
  private suspended = false;
  private state: QualityState = { level: "high", manual: false, conn: "unknown", cameraSuspended: false };
  private ladder = new ScreenLadder();
  private screenAuto = true;
  private screenPrev: { sent: number; lost: number } | undefined;
  private screenBase: Base | undefined;
  private screenId = "";

  /** `onNotice` — сообщение выступающему об изменении качества показа экрана (спокойное: что случилось и что вернётся само). */
  constructor(private room: Room, private onChange: (s: QualityState) => void, private onNotice: (text: string) => void = () => undefined) {}

  start(): void { if (!this.timer) this.timer = setInterval(() => void this.tick(), INTERVAL_MS); }
  stop(): void { if (this.timer) clearInterval(this.timer); this.timer = undefined; }

  /** Ручной выбор уровня (`null` — снова автоматически). */
  async setManual(l: VideoLevel | null): Promise<void> {
    const d = this.ctl.setManual(l);
    if (d.changed) await this.apply(d);
    this.emit({});
  }

  /** Вызывать после включения камеры пользователем: к ней применяется текущий уровень. */
  async onCameraPublished(): Promise<void> {
    this.suspended = false;
    await this.prioritiseAudio();
    if (this.ctl.level !== "off") await this.applyEncodings(this.ctl.level);
    this.emit({});
  }

  private camera(): LocalVideoTrack | undefined {
    return this.room.localParticipant.getTrackPublication(Track.Source.Camera)?.videoTrack as LocalVideoTrack | undefined;
  }

  private async tick(): Promise<void> {
    try {
      const cam = this.camera();
      const conn = this.room.localParticipant.connectionQuality as ConnQuality;
      const sample: Sample = { quality: conn };
      if (cam) {
        const st = await cam.getSenderStats();
        const sent = st.reduce((a, s) => a + (s.packetsSent ?? 0), 0), lost = st.reduce((a, s) => a + (s.packetsLost ?? 0), 0);
        sample.lossPct = lossPercent(this.prev, { sent, lost });
        this.prev = { sent, lost };
        sample.limited = st.find((s) => s.qualityLimitationReason && s.qualityLimitationReason !== "none")?.qualityLimitationReason;
        const rtts = st.map((s) => s.roundTripTime).filter((v): v is number => typeof v === "number");
        if (rtts.length) sample.rttMs = Math.max(...rtts) * 1000;
      }
      const d = this.ctl.push(sample);
      if (d.changed) await this.apply(d);
      await this.tickScreen(conn);
      this.emit({ conn, limited: sample.limited, lossPct: sample.lossPct, rttMs: sample.rttMs });
    } catch { /* замер не удался — повторим на следующем шаге */ }
  }

  // ------------------------------------------------------------------------------------------ показ экрана
  private screen(): LocalVideoTrack | undefined {
    return this.room.localParticipant.getTrackPublication(Track.Source.ScreenShare)?.videoTrack as LocalVideoTrack | undefined;
  }

  getScreenLevel(): number { return this.ladder.level; }

  /** Включить или выключить автоподстройку показа экрана. Выключение возвращает параметры профиля. */
  async setScreenAuto(on: boolean): Promise<void> {
    this.screenAuto = on;
    if (!on) { this.ladder.reset(); await this.applyScreen(0); }
    this.emit({});
  }

  /** Профиль показа изменён (или показ начался заново): прежние параметры больше не «базовые», текущая ступень применяется к новым. */
  async onScreenProfileChanged(): Promise<void> {
    this.screenBase = undefined;
    if (this.ladder.level > 0) await this.applyScreen(this.ladder.level);
  }

  private async tickScreen(conn: ConnQuality): Promise<void> {
    const track = this.screen();
    if (!track) { this.screenPrev = undefined; this.screenId = ""; return; }
    const id = track.mediaStreamTrack?.id ?? "";
    if (id !== this.screenId) { this.screenId = id; this.screenBase = undefined; this.screenPrev = undefined; if (this.screenAuto && this.ladder.level > 0) await this.applyScreen(this.ladder.level); }   // новый показ начинается с прежней ступени, а не с полного качества
    const st = await track.getSenderStats();
    const sent = st.reduce((a, x) => a + (x.packetsSent ?? 0), 0), lost = st.reduce((a, x) => a + (x.packetsLost ?? 0), 0);
    const sample: Sample = { quality: conn, lossPct: lossPercent(this.screenPrev, { sent, lost }) };
    this.screenPrev = { sent, lost };
    sample.limited = st.find((x) => x.qualityLimitationReason && x.qualityLimitationReason !== "none")?.qualityLimitationReason;
    const rtts = st.map((x) => x.roundTripTime).filter((v): v is number => typeof v === "number");
    if (rtts.length) sample.rttMs = Math.max(...rtts) * 1000;
    if (!this.screenAuto) return;
    const d = this.ladder.push(sample);
    if (!d.changed) return;
    const shown = await this.applyScreen(d.level);
    if (d.reason !== "none") this.onNotice(ladderNote(d.reason, d.level, shown));
  }

  /** Применяет ступень к верхнему слою показа экрана. Возвращает, что теперь уходит (разрешение, частота, потолок битрейта) — для сообщения. */
  private async applyScreen(level: number): Promise<string> {
    try {
      const track = this.screen(), sender = track?.sender;
      if (!track || !sender) return "";
      const p = sender.getParameters();
      const e = p.encodings?.[0];
      if (!e) return "";
      this.screenBase ??= { maxBitrate: e.maxBitrate, maxFramerate: e.maxFramerate, scale: e.scaleResolutionDownBy ?? 1 };
      const v = ladderParams(level, this.screenBase);
      e.maxBitrate = v.maxBitrate;
      if (v.maxFramerate) e.maxFramerate = v.maxFramerate;
      e.scaleResolutionDownBy = v.scaleResolutionDownBy;
      await sender.setParameters(p);
      const set = track.mediaStreamTrack?.getSettings?.() ?? {};
      const size = set.width && set.height ? `${Math.round(set.width / v.scaleResolutionDownBy)}×${Math.round(set.height / v.scaleResolutionDownBy)}` : "";
      return [size, v.maxFramerate ? `${v.maxFramerate} к/с` : "", `до ${Math.round(v.maxBitrate / 1000)} кбит/с`].filter(Boolean).join(", ");
    } catch { return ""; }                      // не применилось: повторим на следующей смене ступени
  }

  private async apply(d: Decision): Promise<void> {
    const lp = this.room.localParticipant;
    try {
      const lv = d.level;
      if (!cameraAllowed(lv)) {
        if (lp.isCameraEnabled) { this.suspended = true; await lp.setCameraEnabled(false); }
      } else if (this.suspended) {
        this.suspended = false;
        await lp.setCameraEnabled(true);          // вернули камеру, которую выключили сами
        await this.applyEncodings(lv);
      } else {
        await this.applyEncodings(lv);
      }
    } catch { /* применить не удалось — состояние покажет прежний уровень на следующем замере */ }
    this.emit({});
  }

  private async applyEncodings(level: Exclude<VideoLevel, "off">): Promise<void> {
    const sender = this.camera()?.sender;
    if (!sender) return;
    const p = sender.getParameters();
    if (!p.encodings?.length) return;
    p.encodings = applyLevelToEncodings(p.encodings, level) as RTCRtpEncodingParameters[];
    await sender.setParameters(p);
  }

  /** Звуку — высокий сетевой приоритет, где браузер это поддерживает. */
  private async prioritiseAudio(): Promise<void> {
    const sender = this.room.localParticipant.getTrackPublication(Track.Source.Microphone)?.audioTrack?.sender;
    if (!sender) return;
    try {
      const p = sender.getParameters();
      if (p.encodings?.[0]) { (p.encodings[0] as RTCRtpEncodingParameters & { networkPriority?: string }).networkPriority = "high"; p.encodings[0].priority = "high"; await sender.setParameters(p); }
    } catch { /* не поддерживается */ }
  }

  private emit(extra: Partial<QualityState>): void {
    this.state = { ...this.state, ...extra, level: this.ctl.level, manual: this.ctl.isManual, cameraSuspended: this.suspended, screenLevel: this.ladder.level, screenAuto: this.screenAuto };
    this.onChange(this.state);
  }
}
