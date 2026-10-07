// Запрос–ответ с редактором draw.io через postMessage: «export» (png/svg/xml) отвечает событием `export` с тем же форматом.

export type FrameMsg = { event?: string; patch?: unknown; checksum?: string; checksumMismatch?: boolean; xml?: string; format?: string; data?: string; [k: string]: unknown };
export type ExportFormat = "png" | "svg" | "xml";

export class FrameRpc {
  private waiters = new Map<string, { resolve: (m: FrameMsg | null) => void; timer: unknown }>();

  constructor(
    private post: (m: Record<string, unknown>) => void,
    private setTimer: (fn: () => void, ms: number) => unknown = (f, t) => window.setTimeout(f, t),
    private clearTimer: (h: unknown) => void = (h) => window.clearTimeout(h as number),
  ) {}

  /** Сообщение редактора: true — это ответ на наш запрос. */
  handle(m: FrameMsg): boolean {
    if (m.event !== "export") return false;
    const w = this.waiters.get(String(m.format));
    if (!w) return false;
    this.waiters.delete(String(m.format));
    this.clearTimer(w.timer);
    w.resolve(m);
    return true;
  }

  /** null — редактор не ответил за timeoutMs (xml быстрый; png/svg на большой схеме дольше). */
  request(format: ExportFormat, extra: Record<string, unknown> = {}, timeoutMs = format === "xml" ? 4000 : 30000): Promise<FrameMsg | null> {
    return new Promise((resolve) => {
      const prev = this.waiters.get(format);
      if (prev) { this.clearTimer(prev.timer); prev.resolve(null); }
      const timer = this.setTimer(() => { this.waiters.delete(format); resolve(null); }, timeoutMs);
      this.waiters.set(format, { resolve, timer });
      this.post({ action: "export", format, spinKey: "board", ...extra });
    });
  }

  dispose(): void {
    for (const w of this.waiters.values()) { this.clearTimer(w.timer); w.resolve(null); }
    this.waiters.clear();
  }
}
