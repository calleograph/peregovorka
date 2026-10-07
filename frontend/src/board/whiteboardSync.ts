// Совместная работа над схемой: связывает встроенный редактор draw.io (режим diffSync) с сервером.
//
// Редактор сам умеет «патчи»: при каждой правке присылает `autosave` с патчем и контрольной суммой, а чужой патч принимает действием `patch`.
// Здесь — только обмен: порядок задаёт сервер (сквозной номер `seq`), чужие патчи применяются строго по порядку (опоздавшие ждут пропущенный),
// свои — не применяются повторно, при расхождении контрольных сумм или пропуске номера схема перезагружается с сервера. Полный XML (чтобы схему можно
// было открыть и продолжить редактировать) сохраняется «снимками» после паузы в правках; снимок помечается номером последнего учтённого патча.
// Класс не знает про DOM и сеть (всё внедряется через SyncDeps) — поэтому проверяется обычными модульными тестами.

import type { WhiteboardPatch, WhiteboardState } from "../api";
import { FrameRpc, type ExportFormat, type FrameMsg } from "./frameRpc";

export const EMPTY_XML = '<mxfile><diagram id="p1" name="Страница 1"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>';

export type BoardPhase = "loading" | "ready" | "desync" | "error";
export interface BoardStatus { phase: BoardPhase; message?: string; saved: "saved" | "saving" | "dirty" }

export interface SyncDeps {
  clientId: string;
  readOnly?: boolean;
  fetchState(): Promise<WhiteboardState>;
  sendPatch(body: { patch: unknown; checksum: string | null; client_id: string }): Promise<{ seq: number }>;
  saveSnapshot(xml: string, seq: number): Promise<{ saved: boolean; seq: number }>;
  /** Сообщение редактору (iframe). */
  post(msg: Record<string, unknown>): void;
  onStatus?(s: BoardStatus): void;
  setTimer?(fn: () => void, ms: number): unknown;
  clearTimer?(h: unknown): void;
  random?(): number;
}

const AUTHOR_SAVE_MS = 2500;
const BYSTANDER_SAVE_MS = 9000;
const GAP_WAIT_MS = 1500;
const SEND_RETRIES = [0, 600, 1800];

export class BoardSync {
  private phase: "wait-init" | "fetching" | "loading" | "ready" | "disposed" = "wait-init";
  private lastSeq = 0;                 // все номера ≤ lastSeq уже учтены в редакторе
  private savedSeq = 0;
  private pending = new Map<number, WhiteboardPatch>();
  private own = new Set<number>();
  private inflight = 0;
  private queue: Promise<void> = Promise.resolve();
  private lastRemote = "";
  private dirtyOwn = false;
  private lastOwnXml: string | null = null;
  private mismatches: number[] = [];
  private saveTimer: unknown;
  private gapTimer: unknown;
  private saving = false;
  private rpc: FrameRpc;
  private status: BoardStatus = { phase: "loading", saved: "saved" };

  constructor(private d: SyncDeps) {
    this.rpc = new FrameRpc((m) => this.d.post(m), (fn, ms) => this.timer(fn, ms), (h) => this.clear(h));
  }

  // ----------------------------------------------------------------------------------------- события редактора
  handleFrame(m: FrameMsg): void {
    if (this.phase === "disposed") return;
    switch (m.event) {
      case "init": void this.start(); break;
      case "load": if (this.phase === "loading") { this.phase = "ready"; this.setStatus({ phase: "ready" }); this.drain(); } break;
      case "autosave": this.onAutosave(m); break;
      case "patch": if (m.checksumMismatch) this.onMismatch(); break;
      case "export": this.rpc.handle(m); break;
      default: break;
    }
  }

  /** События сервера: чужие правки и сведения о сохранённых снимках. */
  handleLive(e: { type: string; seq?: number; patch?: unknown; checksum?: string | null; from?: string; by?: string }): void {
    if (this.phase === "disposed") return;
    if (e.type === "whiteboard_patch" && typeof e.seq === "number") {
      if (e.seq > this.lastSeq) this.pending.set(e.seq, { seq: e.seq, patch: e.patch, checksum: e.checksum ?? null, from: e.from ?? "", by: e.by ?? "" });
      this.drain();
    } else if (e.type === "whiteboard_saved" && typeof e.seq === "number") {
      this.savedSeq = Math.max(this.savedSeq, e.seq);
      if (this.savedSeq >= this.lastSeq) { this.dirtyOwn = false; this.clear(this.saveTimer); this.refreshSaved(); }
    }
  }

  /** После переподключения сокета: проверить, не пропущены ли патчи. */
  resyncSoon(): void { if (this.phase === "ready") void this.resync(); }

  /** Перезагрузка схемы с сервера (снимок + патчи после него). Несохранённые локальные правки теряются — это крайняя мера. */
  async resync(): Promise<void> {
    if (this.phase === "disposed") return;
    await this.queue.catch(() => undefined);
    await this.start();
  }

  dispose(): void {
    if (this.phase === "disposed") return;
    // автор последней правки успевает сохранить снимок при закрытии вкладки доски (по последнему известному XML)
    if (this.dirtyOwn && this.lastOwnXml && this.inflight === 0 && !this.d.readOnly) void this.d.saveSnapshot(this.lastOwnXml, this.lastSeq).catch(() => undefined);
    this.phase = "disposed";
    this.clear(this.saveTimer); this.clear(this.gapTimer);
    this.rpc.dispose();
  }

  current(): BoardStatus { return this.status; }
  seq(): number { return this.lastSeq; }

  /** Запрос экспорта у редактора (png/svg/xml); null — редактор не ответил. */
  requestExport(format: ExportFormat, extra: Record<string, unknown> = {}): Promise<FrameMsg | null> {
    return this.rpc.request(format, extra);
  }

  // ----------------------------------------------------------------------------------------------- загрузка
  private async start(): Promise<void> {
    this.phase = "fetching";
    this.setStatus({ phase: "loading" });
    let st: WhiteboardState;
    try { st = await this.d.fetchState(); }
    catch {
      if (this.phase !== "fetching") return;
      this.setStatus({ phase: "error", message: "Не удалось загрузить схему. Повтор…" });
      this.timer(() => { if (this.phase === "fetching") void this.start(); }, 3000);
      return;
    }
    if (this.phase !== "fetching") return;
    this.lastSeq = st.seq; this.savedSeq = st.seq;
    this.own = new Set([...this.own].filter((n) => n > st.seq));
    for (const k of [...this.pending.keys()]) if (k <= st.seq) this.pending.delete(k);
    for (const p of st.patches) if (p.seq > st.seq) this.pending.set(p.seq, p);
    this.dirtyOwn = false;
    this.phase = "loading";
    this.d.post({ action: "load", xml: st.xml ?? EMPTY_XML, autosave: 1, diffSync: true });
  }

  // ------------------------------------------------------------------------------------- применение чужих патчей
  private drain(): void {
    if (this.phase !== "ready") return;
    for (;;) {
      const next = this.lastSeq + 1;
      if (this.own.has(next)) { this.own.delete(next); this.pending.delete(next); this.lastSeq = next; continue; }
      const p = this.pending.get(next);
      if (!p) break;
      this.pending.delete(next);
      this.lastSeq = next;
      if (p.from !== this.d.clientId) { // свою правку (пришедшую по сокету раньше ответа на POST) повторно не применяем
        this.lastRemote = JSON.stringify(p.patch);
        this.d.post({ action: "patch", patch: p.patch, ...(p.checksum ? { checksum: p.checksum } : {}) });
      }
    }
    for (const k of [...this.pending.keys()]) if (k <= this.lastSeq) this.pending.delete(k);
    this.armGap();
    this.scheduleSave();
  }

  /** Пропущен номер: чаще всего он просто опаздывает (публикация идёт параллельно). Ждём немного, затем перезагружаем схему. */
  private armGap(): void {
    if (this.pending.size === 0 || this.inflight > 0) { this.clear(this.gapTimer); this.gapTimer = undefined; return; }
    if (this.gapTimer) return;
    this.gapTimer = this.timer(() => { this.gapTimer = undefined; if (this.pending.size > 0 && this.inflight === 0) void this.resync(); }, GAP_WAIT_MS);
  }

  private onMismatch(): void {
    const now = Date.now();
    this.mismatches = [...this.mismatches.filter((t) => now - t < 20000), now];
    if (this.mismatches.length >= 3) { this.setStatus({ phase: "desync", message: "Схема расходится у участников. Нажмите «Обновить схему»." }); return; }
    void this.resync();
  }

  // ------------------------------------------------------------------------------------------- свои правки
  private onAutosave(m: FrameMsg): void {
    if (this.d.readOnly || this.phase !== "ready") return;
    if (typeof m.xml === "string") this.lastOwnXml = m.xml;
    if (m.patch == null) return;
    const json = JSON.stringify(m.patch);
    if (json === this.lastRemote) { this.lastRemote = ""; return; } // эхо только что применённого чужого патча
    this.dirtyOwn = true;
    this.inflight++;
    this.refreshSaved();
    const body = { patch: m.patch, checksum: typeof m.checksum === "string" ? m.checksum : null, client_id: this.d.clientId };
    this.queue = this.queue.then(async () => {
      let lastErr: unknown;
      for (const wait of SEND_RETRIES) {
        if (wait) await new Promise<void>((r) => this.timer(r, wait));
        if (this.phase === "disposed") return;
        try { const r = await this.d.sendPatch(body); if (r.seq > this.lastSeq) this.own.add(r.seq); return; }
        catch (e) { lastErr = e; }
      }
      throw lastErr;
    }).catch(() => { this.setStatus({ phase: "desync", message: "Часть правок не удалось отправить (нет связи). Нажмите «Обновить схему»." }); })
      .finally(() => { this.inflight--; this.drain(); });
    this.scheduleSave();
  }

  // ------------------------------------------------------------------------------------------------ снимки
  private scheduleSave(): void {
    if (this.d.readOnly || this.phase !== "ready") return;
    if (this.savedSeq >= this.lastSeq && !this.dirtyOwn) { this.refreshSaved(); return; }
    this.clear(this.saveTimer);
    const delay = this.dirtyOwn ? AUTHOR_SAVE_MS : BYSTANDER_SAVE_MS + 4000 * (this.d.random?.() ?? Math.random());
    this.saveTimer = this.timer(() => { void this.trySave(); }, delay);
    this.refreshSaved();
  }

  private async trySave(): Promise<void> {
    if (this.phase !== "ready" || this.saving) return;
    if (this.inflight > 0 || this.pending.size > 0) { this.saveTimer = this.timer(() => { void this.trySave(); }, 1200); return; }
    if (this.savedSeq >= this.lastSeq) { this.dirtyOwn = false; this.refreshSaved(); return; }
    const label = this.lastSeq;
    this.saving = true;
    try {
      const ex = await this.requestExport("xml");
      const xml = typeof ex?.xml === "string" ? ex.xml : null;
      // за время экспорта пришли/ушли правки — снимок не соответствует номеру: повторим позже
      if (!xml || this.lastSeq !== label || this.inflight > 0 || this.pending.size > 0) { this.saveTimer = this.timer(() => { void this.trySave(); }, xml ? 800 : 3000); return; }
      const r = await this.d.saveSnapshot(xml, label);
      this.savedSeq = Math.max(this.savedSeq, r.seq);
      if (this.savedSeq >= this.lastSeq) this.dirtyOwn = false;
    } catch {
      this.saveTimer = this.timer(() => { void this.trySave(); }, 5000);
    } finally {
      this.saving = false;
      this.refreshSaved();
    }
  }

  // ----------------------------------------------------------------------------------------------- служебное
  private refreshSaved(): void {
    const saved = this.saving ? "saving" : (this.dirtyOwn || this.savedSeq < this.lastSeq) ? "dirty" : "saved";
    if (saved !== this.status.saved) this.setStatus({ saved });
  }
  private setStatus(p: Partial<BoardStatus>): void {
    const next = { ...this.status, ...p };
    if (p.phase && p.phase !== "desync" && p.phase !== "error" && !("message" in p)) delete next.message;
    this.status = next;
    this.d.onStatus?.(next);
  }
  private timer(fn: () => void, ms: number): unknown { return (this.d.setTimer ?? ((f, t) => window.setTimeout(f, t)))(fn, ms); }
  private clear(h: unknown): void { if (h !== undefined) (this.d.clearTimer ?? ((x) => window.clearTimeout(x as number)))(h); }
}
