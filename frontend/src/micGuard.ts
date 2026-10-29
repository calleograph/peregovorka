/**
 * Восстановление микрофона во время встречи. LiveKit при «ended» делает ОДНУ попытку открыть устройство по умолчанию и, если не вышло, молча выключает микрофон:
 * пользователь видит «Микрофон выкл.» без объяснения, хотя причина временная (устройство занял другой звонок, гарнитура переподключается, драйвер перезапускается).
 * Здесь — ограниченное число повторных попыток с нарастающей паузой, понятное состояние для интерфейса и немедленная попытка при появлении нового устройства.
 * Чистая логика без DOM и LiveKit: проверяется тестами с поддельным временем. Свободный «чужой» монопольный захват это не обходит — только ждёт его окончания.
 */
export const RECOVERY_DELAYS_MS = [1500, 4000, 8000, 15000, 30000];
/** Сколько звук должен отсутствовать (трек в состоянии mute от системы), чтобы сообщить пользователю. Короткие паузы при переключении устройств не считаются. */
export const SILENT_AFTER_MS = 3000;

export type MicGuardState =
  | { phase: "ok" }
  | { phase: "lost"; attempt: number; max: number; reason: string }
  | { phase: "recovered"; attempts: number }
  | { phase: "failed"; error: unknown; attempts: number }
  | { phase: "silent" };

export interface MicGuardDeps {
  /** Открыть микрофон заново; бросает ошибку, если не удалось. Если микрофон уже работает — просто вернуть управление. */
  reopen(): Promise<void>;
  /** Пользователь хочет говорить (не выключал микрофон сам) и встреча идёт. */
  wanted(): boolean;
  onState(s: MicGuardState): void;
  delays?: number[];
}

export class MicGuard {
  private timer: ReturnType<typeof setTimeout> | undefined;
  private silentTimer: ReturnType<typeof setTimeout> | undefined;
  private attempt = 0;
  private running = false;
  private busy = false;
  private reason = "";

  constructor(private readonly d: MicGuardDeps) {}

  private get delays(): number[] { return this.d.delays ?? RECOVERY_DELAYS_MS; }
  get active(): boolean { return this.running; }

  /** Микрофон потерян (устройство отключено, занято или драйвер сбросил захват): запускает повторные попытки, если они ещё не идут. */
  lost(reason: string): void {
    if (!this.d.wanted()) return;
    this.reason = reason;
    if (this.running) return;
    this.running = true;
    this.attempt = 0;
    this.schedule();
  }

  /** Появилось или изменилось устройство: пробуем сразу, не дожидаясь паузы. */
  retryNow(): void {
    if (!this.running) return;
    clearTimeout(this.timer);
    void this.tryOnce();
  }

  /** Система не отдаёт звук с микрофона (`mute` у дорожки): если это длится дольше порога — сообщаем, вернулся звук — сообщаем об этом. */
  silent(isSilent: boolean): void {
    clearTimeout(this.silentTimer);
    if (isSilent) {
      this.silentTimer = setTimeout(() => { if (this.d.wanted()) this.d.onState({ phase: "silent" }); }, SILENT_AFTER_MS);
    } else if (!this.running) {
      this.d.onState({ phase: "ok" });
    }
  }

  stop(): void {
    clearTimeout(this.timer); clearTimeout(this.silentTimer);
    this.running = false; this.busy = false; this.attempt = 0;
  }

  private schedule(): void {
    const delay = this.delays[Math.min(this.attempt, this.delays.length - 1)];
    this.d.onState({ phase: "lost", attempt: this.attempt + 1, max: this.delays.length, reason: this.reason });
    this.timer = setTimeout(() => void this.tryOnce(), delay);
  }

  private async tryOnce(): Promise<void> {
    if (!this.running || this.busy) return;
    if (!this.d.wanted()) { this.stop(); return; }
    this.busy = true;
    this.attempt++;
    try {
      await this.d.reopen();
    } catch (error) {
      this.busy = false;
      if (!this.running) return;
      if (this.attempt >= this.delays.length) { const attempts = this.attempt; this.stop(); this.d.onState({ phase: "failed", error, attempts }); return; }
      this.schedule();
      return;
    }
    this.busy = false;
    if (!this.running) return;
    const attempts = this.attempt;
    this.stop();
    this.d.onState({ phase: "recovered", attempts });
  }
}
