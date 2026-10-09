/** «Печатает…» в чате: событие не на каждую клавишу, а «началось» + редкий пульс; само гаснет, если ввод прекратился. Ничего не хранится. */
export const TYPING_HEARTBEAT_MS = 3000;     // пока человек печатает, подтверждаем раз в 3 с (не чаще)
export const TYPING_STOP_MS = 4000;          // без ввода столько — отправляем «закончил»
export const TYPING_EXPIRE_MS = 6000;        // у получателя индикатор гаснет сам, если пульс не пришёл (оборвалась связь, закрыли вкладку)

type Timer = ReturnType<typeof setTimeout>;

/** Отправитель: вызывайте `input()` при каждом изменении текста; `stop()` — после отправки сообщения, очистки поля или ухода. */
export class TypingSender {
  private active = false;
  private lastSent = 0;
  private idle: Timer | null = null;

  constructor(private send: (typing: boolean) => void, private now: () => number = () => Date.now(),
              private setT: (fn: () => void, ms: number) => Timer = (fn, ms) => setTimeout(fn, ms), private clearT: (t: Timer) => void = (t) => clearTimeout(t)) {}
  // Ссылки на setTimeout/clearTimeout нельзя класть в поле и вызывать как this.setT(): в браузере это «Illegal invocation» (в Node — нет, поэтому тесты с поддельными таймерами этого не ловили)

  input(): void {
    const t = this.now();
    if (!this.active || t - this.lastSent >= TYPING_HEARTBEAT_MS) {
      this.active = true;
      this.lastSent = t;
      this.send(true);
    }
    if (this.idle) this.clearT(this.idle);
    this.idle = this.setT(() => this.stop(), TYPING_STOP_MS);
  }

  stop(): void {
    if (this.idle) { this.clearT(this.idle); this.idle = null; }
    if (!this.active) return;
    this.active = false;
    this.send(false);
  }
}

/** Получатель: кто сейчас печатает (с учётом срока жизни сигнала). */
export class TypingTracker {
  private who = new Map<string, { name: string; until: number }>();

  event(id: string, name: string, typing: boolean, now = Date.now()): void {
    if (typing) this.who.set(id, { name, until: now + TYPING_EXPIRE_MS });
    else this.who.delete(id);
  }

  names(now = Date.now(), exceptName?: string): string[] {
    for (const [k, v] of this.who) if (v.until <= now) this.who.delete(k);
    return [...this.who.values()].filter((v) => v.name !== exceptName).sort((a, b) => a.until - b.until).map((v) => v.name);
  }
}

/** «Иван Петров печатает…» · «Иван и Анна печатают…» · «Иван Петров и ещё 2 печатают…». */
export function typingText(names: string[]): string {
  if (names.length === 0) return "";
  if (names.length === 1) return `${names[0]} печатает…`;
  if (names.length === 2) return `${names[0]} и ${names[1]} печатают…`;
  return `${names[0]} и ещё ${names.length - 1} печатают…`;
}
