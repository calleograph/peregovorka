import { useEffect, useState } from "react";
import { api, type ApiError, type TotpStatus } from "../api";
import TotpSetup from "./TotpSetup";

/** Экран принудительной настройки 2FA: политика безопасности требует её, а у пользователя она ещё не включена. */
export default function TotpEnrollGate({ onDone, onLogout }: { onDone: () => void; onLogout: () => void }) {
  const [st, setSt] = useState<TotpStatus | null>(null);
  const [err, setErr] = useState("");
  const load = () => { api.totp.status().then((s) => { setSt(s); if (s.enabled) onDone(); }).catch((e) => setErr((e as ApiError).message)); };
  useEffect(load, []);
  return (
    <main className="login-center">
      <div className="card" style={{ maxWidth: 520, margin: "48px auto" }}>
        <h1>Двухфакторная аутентификация</h1>
        {err && <div className="alert error">{err}</div>}
        {st && <TotpSetup status={st} forced onChanged={load} />}
        <p><button className="btn ghost" onClick={onLogout}>Выйти</button></p>
      </div>
    </main>
  );
}
