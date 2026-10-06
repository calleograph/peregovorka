import type { Segment } from "./api";

export type LiveEvent =
  | { type: "segment"; segment: Segment }
  | { type: "participant_joined"; user_id: string; display_name: string }
  | { type: "participant_left"; user_id: string }
  | { type: "meeting_ended"; reason: string }
  | { type: "recording_changed"; enabled: boolean };

/** Состояние соединения с сервером событий: понятно пользователю и видно в панели диагностики. */
export interface SocketStatus {
  state: "connecting" | "online" | "reconnecting" | "offline" | "denied";
  /** Номер текущей попытки переподключения (0 — соединение штатное). */
  attempt: number;
  /** Когда (мс, Date.now) будет следующая попытка; undefined — не запланирована. */
  retryAt?: number;
  message?: string;
}

/** Пауза перед попыткой n (с нуля): экспоненциально 0.5, 1, 2, 4, 8 с … до 15 с, ±30 % случайности, чтобы клиенты не бились одновременно. */
export function backoffDelay(attempt: number, rnd: () => number = Math.random): number {
  const base = Math.min(15000, 500 * 2 ** attempt);
  return Math.round(base * (0.7 + 0.6 * rnd()));
}

const PING_MS = 30000;

/**
 * WebSocket живых событий встречи. Открывается сразу после того, как backend зарегистрировал вход (параллельно с
 * подключением к LiveKit), а не после него: транскрипция и состав участников не ждут медиасоединения.
 * Переподключение — с нарастающей паузой, немедленно при возврате сети/вкладки; «нет доступа» — без повторов.
 */
export class LiveSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private attempt = 0;
  private timer: number | undefined;
  private pinger: number | undefined;
  private everOpened = false;

  constructor(
    private meetingId: string,
    private onEvent: (e: LiveEvent) => void,
    private onStatus: (s: SocketStatus) => void,
    private onSubscribed: () => void = () => undefined,
  ) {}

  start(): void {
    this.closed = false;
    window.addEventListener("online", this.retryNow);
    document.addEventListener("visibilitychange", this.onVisible);
    this.open();
  }

  stop(): void {
    this.closed = true;
    window.clearTimeout(this.timer);
    window.clearInterval(this.pinger);
    window.removeEventListener("online", this.retryNow);
    document.removeEventListener("visibilitychange", this.onVisible);
    this.ws?.close();
    this.ws = null;
  }

  private retryNow = (): void => {
    if (this.closed || (this.ws && this.ws.readyState <= WebSocket.OPEN)) return;
    window.clearTimeout(this.timer);
    this.open();
  };
  private onVisible = (): void => { if (document.visibilityState === "visible") this.retryNow(); };

  private open(): void {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.onStatus({ state: this.attempt === 0 && !this.everOpened ? "connecting" : "reconnecting", attempt: this.attempt });
    const ws = new WebSocket(`${proto}://${location.host}/api/v1/ws`);
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "subscribe", meeting_id: this.meetingId }));
      window.clearInterval(this.pinger);
      this.pinger = window.setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}'); }, PING_MS);
    };
    ws.onmessage = (m) => {
      try {
        const data = JSON.parse(m.data as string);
        if (data.type === "subscribed") {
          this.everOpened = true; this.attempt = 0;
          this.onStatus({ state: "online", attempt: 0 });
          this.onSubscribed();
        } else if (data.type === "error" && data.message === "forbidden") {
          this.closed = true;
          this.onStatus({ state: "denied", attempt: 0, message: "Нет доступа к событиям этой встречи." });
          ws.close();
        } else if (data.type === "segment" || data.type === "participant_joined" || data.type === "participant_left" || data.type === "meeting_ended" || data.type === "recording_changed") {
          this.onEvent(data as LiveEvent);
        }
      } catch { /* игнорируем мусор */ }
    };
    ws.onclose = (ev) => {
      window.clearInterval(this.pinger);
      if (this.closed) return;
      if (ev.code === 4401 || ev.code === 4403) {
        this.closed = true;
        this.onStatus({ state: "denied", attempt: 0, message: ev.code === 4401 ? "Сессия истекла — войдите снова." : "Соединение отклонено сервером." });
        return;
      }
      const delay = backoffDelay(this.attempt++);
      this.onStatus({ state: "reconnecting", attempt: this.attempt, retryAt: Date.now() + delay });
      this.timer = window.setTimeout(() => this.open(), delay);
    };
  }
}
