import type { Segment } from "./api";

export type LiveEvent =
  | { type: "segment"; segment: Segment }
  | { type: "participant_joined"; user_id: string; display_name: string }
  | { type: "participant_left"; user_id: string }
  | { type: "meeting_ended"; reason: string }
  | { type: "recording_changed"; enabled: boolean };

/** WebSocket живых событий встречи: подписка, автопереподключение с растущей паузой. */
export class LiveSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private attempt = 0;
  private timer: number | undefined;

  constructor(
    private meetingId: string,
    private onEvent: (e: LiveEvent) => void,
    private onState: (connected: boolean) => void,
  ) {}

  start(): void { this.closed = false; this.open(); }

  stop(): void {
    this.closed = true;
    window.clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }

  private open(): void {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/v1/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      ws.send(JSON.stringify({ type: "subscribe", meeting_id: this.meetingId }));
    };
    ws.onmessage = (m) => {
      try {
        const data = JSON.parse(m.data as string);
        if (data.type === "subscribed") this.onState(true);
        else if (data.type === "segment" || data.type === "participant_joined" || data.type === "participant_left" || data.type === "meeting_ended" || data.type === "recording_changed")
          this.onEvent(data as LiveEvent);
      } catch { /* игнорируем мусор */ }
    };
    ws.onclose = () => {
      this.onState(false);
      if (this.closed) return;
      const delay = Math.min(15000, 500 * 2 ** this.attempt++);
      this.timer = window.setTimeout(() => this.open(), delay);
    };
  }
}
