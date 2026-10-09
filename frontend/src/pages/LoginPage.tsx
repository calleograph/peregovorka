import { FormEvent, KeyboardEvent, useState } from "react";
import { api, ApiError, type Me } from "../api";
import { CookieNotice, LoginFooter, type BuildInfo } from "../components/ProductInfo";
import { Icon } from "../components/Icons";
import { magnet } from "../fx";
import { isValidLogin, LOGIN_HINT } from "../loginRules";

/** Страница входа: бренд, одна фраза, форма и мелкая служебная строка. Всё остальное — внутри продукта. */
export default function LoginPage({ onLogin, info }: { onLogin: (m: Me) => void; info: BuildInfo | null }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
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
  const checkCaps = (e: KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.("CapsLock") ?? false);

  return (
    <div className="login-simple">
      <main className="login-center">
        <div className="logo login-logo"><span className="logo-mark" aria-hidden><Icon name="video" size={22} /></span> Peregovorka</div>
        <p className="login-tagline">Видеовстречи с автоматической стенограммой и протоколом</p>
        <form className="login-card" onSubmit={submit} aria-label="Вход">
          <label>Логин
            <input value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" autoFocus required maxLength={256}
                   autoCapitalize="none" spellCheck={false} aria-invalid={login !== "" && !valid} placeholder="ivanov" />
            {login !== "" && !valid
              ? <span className="field-err" role="alert">{LOGIN_HINT}</span>
              : <span className="help">Введите обычный доменный логин, без @domain и DOMAIN\</span>}
          </label>
          <label>Пароль
            <span className="pw">
              <input type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} onKeyUp={checkCaps} onKeyDown={checkCaps}
                     autoComplete="current-password" required maxLength={512} />
              <button type="button" className="pw-toggle" onClick={() => setShow((s) => !s)} aria-label={show ? "Скрыть пароль" : "Показать пароль"} aria-pressed={show} title={show ? "Скрыть пароль" : "Показать пароль"}>
                <Icon name={show ? "eyeOff" : "eye"} size={18} />
              </button>
            </span>
            {caps ? <span className="field-warn" role="status">Включён Caps Lock</span> : <span className="help">Ваш доменный пароль</span>}
          </label>
          {error && <div className="alert error" role="alert">{error}</div>}
          <button className="btn primary cta wide" {...magnet} disabled={busy || !valid || !password}>
            {busy ? <><i className="spin" aria-hidden /> Проверка…</> : <>Войти <Icon name="arrowR" size={17} /></>}
          </button>
        </form>
      </main>
      <LoginFooter info={info} />
      <CookieNotice />
    </div>
  );
}
