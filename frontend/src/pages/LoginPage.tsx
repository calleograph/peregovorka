import { FormEvent, KeyboardEvent, Suspense, lazy, useState } from "react";
import { api, ApiError, type Me } from "../api";
import { CookieNotice, LoginFooter, type BuildInfo } from "../components/ProductInfo";
import { Icon } from "../components/Icons";
import { magnet } from "../fx";
import { isValidLogin, LOGIN_HINT } from "../loginRules";
import { useSite } from "../site";

// Фон — только здесь (после входа компонент размонтируется); отдельный чанк, чтобы не утяжелять основной пакет
const LoginBackdrop = lazy(() => import("../components/LoginBackdrop"));

/** Страница входа: бренд, одна фраза, форма и мелкая служебная строка. Всё остальное — внутри продукта. */
export default function LoginPage({ onLogin, info }: { onLogin: (m: Me) => void; info: BuildInfo | null }) {
  const site = useSite();
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
    <div className="login-simple login-px">
      <Suspense fallback={null}><LoginBackdrop /></Suspense>
      <main className="login-center">
        <div className="px-shell">
          <div className="px-brand">
            {site.assets.logo
              ? <img className="px-logo" src={site.assets.logo} alt={site.name} draggable={false} />
              : <><span className="px-mark" aria-hidden><Icon name="video" size={34} /></span><div className="px-name">{site.name}</div></>}
            <p className="px-tag">{site.subtitle}</p>
          </div>
          <form className="login-card px-form" onSubmit={submit} aria-label="Вход">
            {site.welcome_text && <p className="px-welcome">{site.welcome_text}</p>}
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
            <button className="btn primary cta wide px-cta" {...magnet} disabled={busy || !valid || !password}>
              {busy ? <><i className="spin" aria-hidden /> Проверка…</> : <>Войти <Icon name="arrowR" size={17} /></>}
            </button>
          </form>
        </div>
      </main>
      <LoginFooter info={info} />
      <CookieNotice />
    </div>
  );
}
