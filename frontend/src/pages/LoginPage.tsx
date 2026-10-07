import { FormEvent, useState } from "react";
import { api, ApiError, type Me } from "../api";
import { isValidLogin, LOGIN_HINT } from "../loginRules";

export default function LoginPage({ onLogin, version }: { onLogin: (m: Me) => void; version: string }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = isValidLogin(login);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await api.login(login.trim(), password));
    } catch (err) {
      const ae = err as ApiError;
      setError(ae.status === 429 && ae.retryAfter ? `Слишком много неудачных попыток. Повторите через ${Math.max(1, Math.ceil(ae.retryAfter / 60))} мин.` : ae.message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={submit}>
        <h1>Вход в переговорку</h1>
        <p className="muted">Используйте доменную учётную запись.</p>
        <label>Логин
          <input value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" autoFocus required maxLength={256}
                 autoCapitalize="none" spellCheck={false} aria-invalid={login !== "" && !valid} placeholder="ivanov  или  ivanov@corp.local" />
          {login !== "" && !valid && <span className="field-err" role="alert">{LOGIN_HINT}</span>}
        </label>
        <label>Пароль
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required maxLength={512} />
        </label>
        {error && <div className="alert error" role="alert">{error}</div>}
        <button className="btn primary" disabled={busy || !valid || !password}>{busy ? "Проверка…" : "Войти"}</button>
        {version && <div className="muted small">Версия {version}</div>}
      </form>
    </div>
  );
}
