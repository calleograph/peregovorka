import { FormEvent, useEffect, useState } from "react";
import { api, type ApiError, type TotpStatus } from "../api";
import { copyText } from "../util";

/** Настройка двухфакторной аутентификации: пароль → QR-код/ключ → код из приложения → резервные коды (показываются один раз). */
export default function TotpSetup({ status, onChanged, forced = false }: { status: TotpStatus; onChanged: () => void; forced?: boolean }) {
  const [step, setStep] = useState<"idle" | "scan" | "codes">("idle");
  const [pw, setPw] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"" | "disable" | "regen">("");
  useEffect(() => { setErr(""); }, [step, mode]);

  const run = async (fn: () => Promise<void>) => { setBusy(true); setErr(""); try { await fn(); } catch (e) { setErr((e as ApiError).message); } setBusy(false); };
  const start = (e: FormEvent) => { e.preventDefault(); void run(async () => { setSetup(await api.totp.setup(pw)); setPw(""); setStep("scan"); }); };
  const enable = (e: FormEvent) => { e.preventDefault(); void run(async () => { setCodes((await api.totp.enable(code)).backup_codes); setCode(""); setSetup(null); setStep("codes"); }); };
  const confirmMode = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      if (mode === "disable") { await api.totp.disable(pw, code); setMode(""); onChanged(); }
      else { setCodes((await api.totp.regenerate(pw, code)).backup_codes); setMode(""); setStep("codes"); }
      setPw(""); setCode("");
    });
  };

  if (step === "codes") {
    return (
      <div>
        <div className="alert warn" role="alert"><b>Резервные коды — сохраните их сейчас.</b> Каждый подходит один раз, если не будет доступа к приложению. Показываются только сейчас.</div>
        <ul className="backup-codes" aria-label="Резервные коды">{codes.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
        <div className="row">
          <button className="btn" onClick={() => void copyText(codes.join("\n"))}>Копировать</button>
          <label className="check"><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> Я сохранил(а) коды</label>
          <button className="btn primary" disabled={!saved} onClick={() => { setStep("idle"); setSaved(false); setCodes([]); onChanged(); }}>Готово</button>
        </div>
      </div>
    );
  }
  if (step === "scan" && setup) {
    return (
      <form className="form" onSubmit={enable}>
        <p>1. Откройте приложение-аутентификатор (Google Authenticator, Microsoft Authenticator, FreeOTP, Aegis) и отсканируйте код.</p>
        <div className="totp-qr" role="img" aria-label="QR-код для приложения-аутентификатора" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
        <p className="small muted">Нет камеры? Введите ключ вручную (тип «по времени»): <code style={{ userSelect: "all" }}>{setup.secret}</code></p>
        <p>2. Введите шестизначный код из приложения.</p>
        <label>Код<input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={8} autoFocus required /></label>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row"><button className="btn primary" disabled={busy || code.trim().length < 6}>Включить</button>{!forced && <button type="button" className="btn" onClick={() => { setStep("idle"); setSetup(null); }}>Отмена</button>}</div>
      </form>
    );
  }
  if (mode) {
    return (
      <form className="form" onSubmit={confirmMode}>
        <p>{mode === "disable" ? "Чтобы отключить двухфакторную защиту, подтвердите пароль и введите текущий код." : "Новые резервные коды заменят прежние. Подтвердите пароль и введите текущий код из приложения."}</p>
        <label>Пароль<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required /></label>
        <label>Код из приложения<input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={32} required /></label>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row"><button className={`btn ${mode === "disable" ? "danger" : "primary"}`} disabled={busy || !pw || !code}>{mode === "disable" ? "Отключить" : "Создать коды"}</button><button type="button" className="btn" onClick={() => setMode("")}>Отмена</button></div>
      </form>
    );
  }
  if (status.enabled) {
    return (
      <div>
        <p>Включена{status.enabled_at ? ` с ${new Date(status.enabled_at).toLocaleDateString("ru-RU")}` : ""}. Неиспользованных резервных кодов: <b>{status.backup_left}</b>.</p>
        {status.backup_left <= 2 && <div className="alert warn">Резервных кодов осталось мало — создайте новые.</div>}
        <div className="row"><button className="btn" onClick={() => setMode("regen")}>Создать новые резервные коды</button>
          {status.required ? <span className="muted small">Отключить нельзя: двухфакторная защита обязательна по политике безопасности.</span> : <button className="btn ghost danger" onClick={() => setMode("disable")}>Отключить</button>}</div>
      </div>
    );
  }
  return (
    <form className="form" onSubmit={start}>
      <p>{forced ? "Политика безопасности требует двухфакторную защиту: пока она не настроена, остальные разделы недоступны." : "Дополнительная защита входа: после пароля нужен одноразовый код из приложения на телефоне."}</p>
      <label>Ваш пароль<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required autoFocus={forced} /></label>
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="row"><button className="btn primary" disabled={busy || !pw}>Настроить</button></div>
    </form>
  );
}
