// Токен доверенного устройства каждой организации — отдельный файл, зашифрованный средствами ОС (Windows: DPAPI текущего пользователя через safeStorage).
// Пароль нигде не хранится. Нет защищённого хранилища — токен не сохраняется вовсе (вход в пределах серверного сеанса), открытым текстом — никогда.
import { safeStorage } from "electron";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export class TrustStore {
  private dir: string;
  constructor(userData: string, private allowBasicForTests: boolean) {
    this.dir = join(userData, "trust");
  }

  available(): boolean {
    if (!safeStorage.isEncryptionAvailable()) return false;
    if (process.platform === "win32" || process.platform === "darwin") return true;
    return safeStorage.getSelectedStorageBackend() !== "basic_text" || this.allowBasicForTests;    // basic_text в Linux — не шифрование
  }

  private file(orgId: string): string {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(orgId)) throw new Error("bad_org_id");
    return join(this.dir, `${orgId}.bin`);
  }

  get(orgId: string): string | null {
    if (!this.available()) return null;
    try { return safeStorage.decryptString(readFileSync(this.file(orgId))); } catch { return null; }
  }

  set(orgId: string, token: string): boolean {
    if (!this.available()) return false;
    mkdirSync(this.dir, { recursive: true });
    writeFileSync(this.file(orgId), safeStorage.encryptString(token), { mode: 0o600 });
    return true;
  }

  delete(orgId: string): void {
    try { rmSync(this.file(orgId), { force: true }); } catch { /* уже нет */ }
  }
}
