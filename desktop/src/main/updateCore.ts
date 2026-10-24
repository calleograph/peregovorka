// Проверка обновлений без Electron (проверяется тестами): манифест подписан ключом издателя Ed25519 — его подлинность не зависит от того, откуда он скачан
// (GitHub Releases или корпоративное зеркало), а установочный файл сверяется по SHA-256 из подписанного манифеста. Без подписи или с чужим ключом — отказ.
import { createHash, createPublicKey, verify } from "node:crypto";
import { cmpVersion } from "../common/version";

export type UpdateFile = { kind: "nsis"; url: string; sha256: string; size: number; name: string };
export type Manifest = { product: "peregovorka-desktop"; version: string; released: string; notes?: string; files: UpdateFile[] };

/** Открытый ключ в виде base64 «сырых» 32 байт Ed25519 → KeyObject. */
export function publicKey(b64: string) {
  const raw = Buffer.from(b64, "base64");
  if (raw.length !== 32) throw new Error("bad_key");
  return createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), raw]), format: "der", type: "spki" });
}

export function verifyManifest(body: Buffer, sigB64: string, keyB64: string): Manifest {
  if (!keyB64) throw new Error("no_key");
  const sig = Buffer.from(sigB64.trim(), "base64");
  if (sig.length !== 64 || !verify(null, body, publicKey(keyB64), sig)) throw new Error("bad_signature");
  const m = JSON.parse(body.toString("utf8")) as Manifest;
  if (m.product !== "peregovorka-desktop" || !/^\d+\.\d+\.\d+$/.test(m.version) || !Array.isArray(m.files)) throw new Error("bad_manifest");
  for (const f of m.files) {
    if (f.kind !== "nsis" || !/^https:\/\//.test(f.url) || !/^[0-9a-f]{64}$/.test(f.sha256) || !(f.size > 0 && f.size < 1_000_000_000) || !/^[\w .()-]+\.exe$/.test(f.name)) throw new Error("bad_manifest");
  }
  return m;
}

export const newer = (m: Manifest, current: string) => cmpVersion(m.version, current) > 0;

export function sha256File(buf: Buffer): string {
  return createHash("sha256").update(buf).digest("hex");
}

/** Можно ли перезапускать для установки сейчас: никогда во время встречи или показа экрана. */
export const canRestartNow = (orgs: { inCall: boolean; sharing: boolean }[]) => !orgs.some((o) => o.inCall || o.sharing);
