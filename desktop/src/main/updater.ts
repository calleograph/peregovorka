// Собственный механизм обновлений. Источник — канал выпусков издателя (или корпоративное зеркало из политики UpdateUrl); доверие — только подписи:
// манифест подписан ключом Ed25519 издателя (открытый ключ встроен в сборку), пакет сверяется по SHA-256 из манифеста, а на Windows подпись Authenticode
// нового установщика должна совпадать с подписью установленной программы. Адрес сервера организации здесь не участвует вовсе.
// Во время встречи и показа экрана перезапуск не выполняется; установленное через MSI обновляет администратор (обновления «управляются централизованно»).
import { app, net } from "electron";
import { spawn, execFile } from "node:child_process";
import { createWriteStream, mkdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { join } from "node:path";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import type { Ctx } from "./ctx";
import { newer, sha256File, verifyManifest, canRestartNow, type Manifest } from "./updateCore";

export type UpdateState = { status: "idle" | "checking" | "none" | "available" | "downloading" | "ready" | "error" | "disabled" | "managed"; version?: string; notes?: string;
  progress?: number; error?: string; checkedAt?: number; installOnQuit?: boolean };

declare const __UPDATE_PUBKEY__: string;
declare const __UPDATE_FEED__: string;

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
  private timer: NodeJS.Timeout | null = null;
  constructor(private ctx: Ctx, private orgsState: () => { inCall: boolean; sharing: boolean }[], private managedInstall: boolean, private changed: () => void) {}

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
    else if (m === "nokey") this.set({ status: "disabled", error: "В этой сборке не настроен ключ проверки обновлений: новые версии устанавливаются установщиком." });
    else if (m === "off") this.set({ status: "disabled", error: undefined });
    if (this.timer) clearInterval(this.timer);
    if (m === "auto" || m === "notify") {
      setTimeout(() => void this.check(false), 60_000);
      this.timer = setInterval(() => void this.check(false), 6 * 3600_000);
    }
  }

  async check(manual: boolean): Promise<UpdateState> {
    const m = this.mode();
    if (m === "managed") { this.set({ status: "managed" }); return this.state; }
    if (m === "nokey") { this.schedule(); return this.state; }
    if (m === "off" && !manual) return this.state;
    if (this.state.status === "downloading" || this.state.status === "ready") return this.state;
    this.set({ status: "checking", error: undefined });
    try {
      const feed = this.feed();
      const [body, sig] = await Promise.all([fetchBuf(feed, 200_000), fetchBuf(`${feed}.sig`, 1000)]);
      const man = verifyManifest(body, sig.toString("utf8"), __UPDATE_PUBKEY__);
      if (!newer(man, this.ctx.version)) { this.set({ status: "none", checkedAt: Date.now() }); return this.state; }
      this.set({ status: "available", version: man.version, notes: (man.notes || "").slice(0, 4000), checkedAt: Date.now() });
      this.ctx.log.info("update.available", { version: man.version });
      if (m === "auto" || manual) await this.download(man);
    } catch (e) {
      const msg = String((e as Error)?.message ?? e);
      this.ctx.log.warn("update.check_failed", { error: msg.slice(0, 120) });
      this.set({ status: "error", error: msg === "bad_signature" ? "Подпись обновления не прошла проверку — обновление отклонено." : "Не удалось проверить обновления. Повторите позже.", checkedAt: Date.now() });
    }
    return this.state;
  }

  private async download(man: Manifest): Promise<void> {
    const f = man.files.find((x) => x.kind === "nsis");
    if (!f || process.platform !== "win32") { this.set({ status: "available" }); return; }
    const dir = join(app.getPath("temp"), "peregovorka-update");
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(dir, { recursive: true });
    const path = join(dir, f.name);
    this.set({ status: "downloading", progress: 0 });
    const r = await net.fetch(f.url, { cache: "no-store" } as RequestInit);
    if (!r.ok || !r.body) throw new Error(`http_${r.status}`);
    let got = 0;
    const src = Readable.fromWeb(r.body as never);
    src.on("data", (c: Buffer) => { got += c.length; if (got > f.size) src.destroy(new Error("size_mismatch")); this.state.progress = Math.round((got / f.size) * 100); });
    await pipeline(src, createWriteStream(path));
    if (statSync(path).size !== f.size || sha256File(readFileSync(path)) !== f.sha256) { rmSync(path, { force: true }); throw new Error("hash_mismatch"); }
    const mine = await authenticode(process.execPath);
    if (mine.status === "Valid") {
      const theirs = await authenticode(path);
      if (theirs.status !== "Valid" || theirs.subject !== mine.subject) { rmSync(path, { force: true }); throw new Error("signature_mismatch"); }
    }
    this.file = path;
    this.set({ status: "ready", progress: 100 });
    this.ctx.log.info("update.ready", { version: man.version });
  }

  canRestart(): boolean { return canRestartNow(this.orgsState()); }

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

  private run(relaunch: boolean): void {
    const args = ["/S", "--updated", ...(relaunch ? ["--force-run"] : [])];
    this.ctx.log.info("update.install", { relaunch });
    spawn(this.file!, args, { detached: true, stdio: "ignore", windowsHide: true }).unref();
    this.file = null;
    if (relaunch) { this.ctx.quitting = true; app.quit(); }
  }
}
