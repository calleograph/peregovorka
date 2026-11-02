// Собственный механизм обновлений. Источник — канал выпусков издателя (или корпоративное зеркало из политики UpdateUrl); доверие — только подписи:
// манифест подписан ключом Ed25519 издателя (открытый ключ встроен в сборку), пакет сверяется по SHA-256 из манифеста, а на Windows подпись Authenticode
// нового установщика должна совпадать с подписью установленной программы. Адрес сервера организации здесь не участвует вовсе.
// Во время встречи и показа экрана перезапуск не выполняется; установленное через MSI обновляет администратор (обновления «управляются централизованно»).
import { app, net } from "electron";
import { spawn, execFile } from "node:child_process";
import { createWriteStream, mkdirSync, readFileSync, rmSync, statSync, statfsSync } from "node:fs";
import { join } from "node:path";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import type { Ctx } from "./ctx";
import { newer, sha256File, verifyManifest, canRestartNow, relaunchCommand, type Manifest } from "./updateCore";

export type UpdateState = { status: "idle" | "checking" | "none" | "available" | "downloading" | "ready" | "error" | "disabled" | "managed" | "restart"; version?: string; notes?: string;
  progress?: number; error?: string; checkedAt?: number; installOnQuit?: boolean; latest?: string; manualUrl?: string | null };

const ERRORS: Record<string, string> = {
  bad_signature: "Подпись описания обновления не прошла проверку — обновление отклонено.",
  bad_manifest: "Описание обновления повреждено — обновление отклонено.",
  hash_mismatch: "Загруженный установщик не совпал с подписанным описанием выпуска и удалён.",
  size_mismatch: "Загруженный установщик не совпал с подписанным описанием выпуска и удалён.",
  signature_mismatch: "Подпись установщика не совпадает с подписью установленной программы — установщик удалён.",
  no_space: "Недостаточно места на диске для загрузки обновления.",
  spawn_failed: "Не удалось запустить установщик. Скачайте его вручную или обратитесь к администратору.",
};

declare const __UPDATE_PUBKEY__: string;
declare const __UPDATE_FEED__: string;
declare const __DOWNLOAD_PAGE__: string;

async function fetchBuf(url: string, limit: number): Promise<Buffer> {
  const r = await net.fetch(url, { cache: "no-store" } as RequestInit);
  if (!r.ok) throw new Error(`http_${r.status}`);
  const b = Buffer.from(await r.arrayBuffer());
  if (b.length > limit) throw new Error("too_large");
  return b;
}

function authenticode(file: string): Promise<{ status: string; subject: string }> {
  return new Promise((res) => {
    const ps = `$s = Get-AuthenticodeSignature -LiteralPath '${file.replace(/'/g, "''")}'; Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)`;
    execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps], { windowsHide: true, timeout: 20000 }, (_e, out) => {
      const [status, subject] = String(out || "").trim().split("|");
      res({ status: status || "Unknown", subject: subject || "" });
    });
  });
}

export class Updater {
  state: UpdateState = { status: "idle" };
  private file: string | null = null;
  private man: Manifest | null = null;
  private timer: NodeJS.Timeout | null = null;
  private told = "";
  constructor(private ctx: Ctx, private orgsState: () => { inCall: boolean; sharing: boolean }[], private managedInstall: boolean, private changed: () => void,
    private announce: (version: string) => void) {}

  /** Страница, где установщик можно скачать вручную: из подписанного манифеста, иначе — страница выпусков издателя (не для корпоративного зеркала). */
  private manualUrl(): string | null {
    const p = this.man?.page;
    if (p && /^https:\/\/[^\s]+$/.test(p)) return p;
    return this.ctx.policy.updateUrl ? null : __DOWNLOAD_PAGE__ || null;
  }

  private set(s: Partial<UpdateState>): void { this.state = { ...this.state, ...s }; this.changed(); }
  private feed(): string { return this.ctx.policy.updateUrl || __UPDATE_FEED__; }

  mode(): "auto" | "notify" | "off" | "managed" | "nokey" {
    if (this.managedInstall) return "managed";
    if (!__UPDATE_PUBKEY__ || !this.feed()) return "nokey";
    return this.ctx.eff().updateMode;
  }

  schedule(): void {
    const m = this.mode();
    if (m === "managed") this.set({ status: "managed" });
    else if (m === "nokey") this.set({ status: "disabled", error: "В этой сборке не настроен ключ проверки обновлений: новую версию скачайте и установите поверх текущей.", manualUrl: this.manualUrl() });
    else if (m === "off") this.set({ status: "disabled", error: undefined });
    if (this.timer) clearInterval(this.timer);
    if (m === "auto" || m === "notify") {
      setTimeout(() => void this.check(false), 60_000);
      this.timer = setInterval(() => void this.check(false), 6 * 3600_000);
    }
  }

  async check(manual: boolean): Promise<UpdateState> {
    const m = this.mode();
    if (m === "managed") { if (this.state.status !== "restart") this.set({ status: "managed" }); return this.state; }
    if (m === "nokey") { this.schedule(); return this.state; }
    if (m === "off" && !manual) return this.state;
    if (this.state.status === "downloading" || this.state.status === "ready") return this.state;
    this.set({ status: "checking", error: undefined });
    try {
      const feed = this.feed();
      const [body, sig] = await Promise.all([fetchBuf(feed, 200_000), fetchBuf(`${feed}.sig`, 1000)]);
      const man = verifyManifest(body, sig.toString("utf8"), __UPDATE_PUBKEY__);
      this.man = man;
      if (!newer(man, this.ctx.version)) { this.set({ status: "none", latest: man.version, checkedAt: Date.now(), manualUrl: this.manualUrl() }); return this.state; }
      this.set({ status: "available", version: man.version, latest: man.version, notes: (man.notes || "").slice(0, 4000), checkedAt: Date.now(), manualUrl: this.manualUrl() });
      this.ctx.log.info("update.available", { version: man.version });
      if (!manual && this.told !== man.version) { this.told = man.version; this.announce(man.version); }
      if (m === "auto") await this.download();
    } catch (e) {
      this.fail("update.check_failed", e, "Не удалось проверить обновления: нет связи с каналом обновлений. Повторите позже.");
    }
    return this.state;
  }

  private fail(event: string, e: unknown, fallback: string): void {
    const msg = String((e as Error)?.message ?? e);
    this.ctx.log.warn(event, { error: msg.slice(0, 120) });
    this.set({ status: "error", error: ERRORS[msg] ?? fallback, checkedAt: Date.now(), progress: undefined, manualUrl: this.manualUrl() });
  }

  /** «Скачать обновление»: загрузка установщика с повтором после сетевой ошибки, затем проверка размера, SHA-256 и подписи. */
  async download(): Promise<UpdateState> {
    const man = this.man;
    if (!man || this.state.status === "downloading" || this.state.status === "ready") return this.state;
    const f = man.files.find((x) => x.kind === "nsis");
    if (!f || process.platform !== "win32") return this.state;
    try {
      const dir = join(app.getPath("temp"), "peregovorka-update");
      rmSync(dir, { recursive: true, force: true });
      mkdirSync(dir, { recursive: true });
      const fs = statfsSync(dir);
      if (fs.bavail * fs.bsize < f.size * 2) throw new Error("no_space");          // запас: установщик распаковывает программу рядом
      const path = join(dir, f.name);
      // корпоративное зеркало (политика UpdateUrl) хранит установщик рядом с манифестом; подлинность — по SHA-256 из подписанного манифеста
      const url = this.ctx.policy.updateUrl ? new URL(f.name, this.feed()).href : f.url;
      this.set({ status: "downloading", progress: 0, error: undefined });
      for (let attempt = 1; ; attempt++) {
        try { await this.fetchTo(url, f.size, path); break; } catch (e) {
          const msg = String((e as Error)?.message ?? e);
          if (attempt >= 3 || msg === "size_mismatch") throw e;
          this.ctx.log.warn("update.download_retry", { attempt, error: msg.slice(0, 80) });
          await new Promise((r) => setTimeout(r, attempt * 5000));
        }
      }
      if (statSync(path).size !== f.size || sha256File(readFileSync(path)) !== f.sha256) { rmSync(path, { force: true }); throw new Error("hash_mismatch"); }
      const mine = await authenticode(process.execPath);
      if (mine.status === "Valid") {
        const theirs = await authenticode(path);
        if (theirs.status !== "Valid" || theirs.subject !== mine.subject) { rmSync(path, { force: true }); throw new Error("signature_mismatch"); }
      }
      this.file = path;
      this.set({ status: "ready", progress: 100 });
      this.ctx.log.info("update.ready", { version: man.version });
    } catch (e) {
      this.fail("update.download_failed", e, "Не удалось загрузить обновление. Проверьте связь и повторите или скачайте установщик вручную.");
    }
    return this.state;
  }

  private async fetchTo(url: string, size: number, path: string): Promise<void> {
    const r = await net.fetch(url, { cache: "no-store" } as RequestInit);
    if (!r.ok || !r.body) throw new Error(`http_${r.status}`);
    let got = 0;
    const src = Readable.fromWeb(r.body as never);
    src.on("data", (c: Buffer) => {
      got += c.length;
      if (got > size) src.destroy(new Error("size_mismatch"));
      const p = Math.round((got / size) * 100);
      if (p !== this.state.progress) { this.state.progress = p; this.changed(); }
    });
    await pipeline(src, createWriteStream(path));
  }

  canRestart(): boolean { return canRestartNow(this.orgsState()); }

  /** Linux: менеджер пакетов (apt upgrade) уже заменил файлы программы, а работает прежняя версия — до перезапуска новые окна и процессы
   *  смешали бы версии. Состояние «нужен перезапуск»; перезапуск — кнопкой, только вне встречи и показа экрана. */
  packageUpgraded(version: string): void {
    if (this.state.status === "restart" && this.state.version === version) return;
    this.ctx.log.info("update.package_installed", { version });
    this.set({ status: "restart", version, latest: version, error: undefined, checkedAt: Date.now() });
  }

  relaunch(): boolean {
    if (this.state.status !== "restart" || !this.canRestart()) return false;
    this.ctx.log.info("update.relaunch", { version: this.state.version });
    // Не app.relaunch(): после apt upgrade исполняемый файл заменён, и встроенный перезапуск Electron берёт путь из /proc/self/exe —
    // «/opt/Peregovorka/peregovorka (deleted)»: новая версия не запускалась. Запускаем сами по постоянному пути, когда этот процесс завершится
    // (две копии одновременно не открывают один профиль). Унаследованные дескрипторы прежнего процесса закрываются: часть сокетов Chromium открывает без
    // FD_CLOEXEC, и новая копия держала бы их (найдено испытанием: прежний сокет отладки оставался открытым и не обслуживался).
    const script = 'for f in /proc/$$/fd/*; do n=${f##*/}; [ "$n" -gt 2 ] 2>/dev/null && eval "exec $n>&-" 2>/dev/null; done; while kill -0 "$0" 2>/dev/null; do sleep 0.2; done; exec "$@"';
    const child = spawn("/bin/bash", ["-c", script, String(process.pid), ...relaunchCommand(process.execPath, process.argv)], { detached: true, stdio: "ignore" });
    child.unref();
    this.ctx.quitting = true;
    app.quit();
    return true;
  }

  /** Перезапустить и установить сейчас (только вне встречи) или отметить установку при выходе из приложения. */
  install(now: boolean): boolean {
    if (!this.file) return false;
    if (!now) { this.set({ installOnQuit: true }); return true; }
    if (!this.canRestart()) return false;
    this.run(true);
    return true;
  }

  /** Вызывается при выходе: если обновление отложено «до выхода» — тихая установка без перезапуска. */
  onQuit(): void { if (this.file && this.state.installOnQuit) this.run(false); }

  /** Установщик обновляет программу поверх (данные пользователя в %APPDATA% не трогает) и с --force-run запускает её снова. Выход — только после того,
   *  как установщик действительно запустился: иначе приложение осталось бы закрытым без обновления. */
  private run(relaunch: boolean): void {
    const args = ["/S", "--updated", ...(relaunch ? ["--force-run"] : [])];
    this.ctx.log.info("update.install", { relaunch });
    const file = this.file!;
    const child = spawn(file, args, { detached: true, stdio: "ignore", windowsHide: true });
    child.once("error", (e) => { this.file = file; this.fail("update.spawn_failed", new Error("spawn_failed"), ERRORS.spawn_failed); this.ctx.log.warn("update.spawn_error", { error: String(e).slice(0, 120) }); });
    child.once("spawn", () => {
      child.unref();
      this.file = null;
      if (relaunch) { this.ctx.quitting = true; app.quit(); }
    });
  }
}
