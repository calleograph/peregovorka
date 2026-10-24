// Локальный журнал: %APPDATA%\Peregovorka\logs\main.log, ротация по размеру (5 файлов по 1 МБ — не более 5 МБ на диске), секреты маскируются до записи.
// Содержимое сообщений чатов в журнал не пишется: только события и коды ошибок.
import { appendFileSync, mkdirSync, renameSync, rmSync, statSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { redact } from "../common/redact";

export class Log {
  readonly file: string;
  constructor(readonly dir: string, private maxBytes = 1_000_000, private keep = 5) {
    mkdirSync(dir, { recursive: true });
    this.file = join(dir, "main.log");
  }

  private rotate(): void {
    try {
      if (!existsSync(this.file) || statSync(this.file).size < this.maxBytes) return;
      rmSync(`${this.file}.${this.keep - 1}`, { force: true });
      for (let i = this.keep - 2; i >= 1; i--) if (existsSync(`${this.file}.${i}`)) renameSync(`${this.file}.${i}`, `${this.file}.${i + 1}`);
      renameSync(this.file, `${this.file}.1`);
    } catch { /* журнал не должен ронять приложение */ }
  }

  write(level: "info" | "warn" | "error", event: string, data?: Record<string, unknown>): void {
    let extra = "";
    try { extra = data ? " " + JSON.stringify(data) : ""; } catch { extra = " (данные не сериализуются)"; }
    const line = redact(`${new Date().toISOString()} ${level.toUpperCase()} ${event}${extra}`).slice(0, 4000) + "\n";
    try {
      this.rotate();
      appendFileSync(this.file, line, "utf8");
    } catch { /* диск заполнен или нет прав — работаем без журнала */ }
    if (process.env.PEREGOVORKA_LOG_STDERR) process.stderr.write(line);
  }
  info = (e: string, d?: Record<string, unknown>) => this.write("info", e, d);
  warn = (e: string, d?: Record<string, unknown>) => this.write("warn", e, d);
  error = (e: string, d?: Record<string, unknown>) => this.write("error", e, d);

  tail(lines = 300): string {
    try { return redact(readFileSync(this.file, "utf8").split("\n").slice(-lines).join("\n")); } catch { return ""; }
  }
}
