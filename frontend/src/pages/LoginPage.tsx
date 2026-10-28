import { FormEvent, KeyboardEvent, Suspense, lazy, useState } from "react";
import { api, ApiError, type Me, type SessionLimitInfo, type TotpChallenge } from "../api";
import { fmt } from "../util";
import { CookieNotice, LoginFooter, type BuildInfo } from "../components/ProductInfo";
import { Icon } from "../components/Icons";
import { magnet } from "../fx";
import { isValidLogin, LOGIN_HINT } from "../loginRules";
import { useSite } from "../site";

// Фон — только здесь (после входа компонент размонтируется); отдельный чанк, чтобы не утяжелять основной пакет
const LoginBackdrop = lazy(() => import("../components/LoginBackdrop"));
const LoginNetwork = lazy(() => import("../components/LoginNetwork"));          // лёгкий SVG-фон «встреча на связи»

/** Страница входа: бренд, одна фраза, форма и мелкая служебная строка. Всё остальное — внутри продукта. */
export default function LoginPage({ onLogin, info }: { onLogin: (m: Me) => void; info: BuildInfo | null }) {
  const site = useSite();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");
  const [limit, setLimit] = useState<SessionLimitInfo | null>(null);
  const valid = isValidLogin(login);
  const onFail = (ae: ApiError) => {
    if (ae.code === "session_limit" && ae.extra) { setLimit({ ...(ae.extra as unknown as SessionLimitInfo), message: ae.message }); setPassword(""); setChallenge(""); setCode(""); return true; }
    return false;
  };
  const free = async (id: string) => {
    if (!limit) return;
    setBusy(true); setError("");
    try { onLogin(await api.freeSlot(limit.free_token, id)); }
    catch (err) { setError((err as ApiError).message); if ((err as ApiError).code === "free_expired") setLimit(null); }
    finally { setBusy(false); }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api.login(login.trim(), password);
      if ((r as TotpChallenge).totp_required) { setChallenge((r as TotpChallenge).challenge); setPassword(""); } else onLogin(r as Me);
    } catch (err) {
      const ae = err as ApiError;
      if (onFail(ae)) return;
      setError(ae.status === 429 && ae.retryAfter ? `Слишком много неудачных попыток. Повторите через ${Math.max(1, Math.ceil(ae.retryAfter / 60))} мин.` : ae.message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };
  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try { onLogin(await api.loginTotp(challenge, code.trim())); }
    catch (err) {
      const ae = err as ApiError;
      if (onFail(ae)) return;
      setError(ae.message);
      setCode("");
      if (ae.code === "totp_expired") { setChallenge(""); }
    } finally { setBusy(false); }
  };
  const checkCaps = (e: KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.("CapsLock") ?? false);

  return (
    <div className="login-simple login-px">
      <Suspense fallback={null}><LoginBackdrop /><LoginNetwork /></Suspense>
      <main className="login-center">
        <div className="px-shell">
          <div className="px-brand">
            {site.assets.logo
              ? <img className="px-logo" src={site.assets.logo} alt={site.name} draggable={false} />
              : <><span className="px-mark" aria-hidden><Icon name="video" size={34} /></span><div className="px-name">{site.name}</div></>}
            <p className="px-tag">{site.subtitle}</p>
          </div>
          {limit ? (
            <div className="login-card px-form" role="dialog" aria-label="Слишком много активных сеансов">
              <p className="px-welcome">{limit.message}</p>
              <ul className="sess-list">
                {limit.sessions.map((s) => (
                  <li key={s.id}><span><b>{s.device}</b><small className="muted">{[s.ip || "адрес не определён", s.last_seen ? `активность ${fmt(new Date(s.last_seen * 1000).toISOString())}` : ""].filter(Boolean).join(" · ")}</small></span>
                    <button className="btn primary mini" disabled={busy} onClick={() => void free(s.id)}>Завершить и войти здесь</button></li>))}
              </ul>
              {error && <div className="alert error" role="alert">{error}</div>}
              <button type="button" className="btn ghost" onClick={() => { setLimit(null); setError(""); }}>Отмена</button>
            </div>
          ) : challenge ? (
            <form className="login-card px-form" onSubmit={submitCode} aria-label="Подтверждение входа">
              <p className="px-welcome">Введите шестизначный код из приложения-аутентификатора или резервный код.</p>
              <label>Код подтверждения
                <input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" inputMode="text" autoFocus required maxLength={32} autoCapitalize="none" spellCheck={false} placeholder="123456" />
              </label>
              {error && <div className="alert error" role="alert">{error}</div>}
              <button className="btn primary cta wide px-cta" disabled={busy || code.trim().length < 6}>{busy ? <><i className="spin" aria-hidden /> Проверка…</> : <>Подтвердить <Icon name="arrowR" size={17} /></>}</button>
              <button type="button" className="btn ghost" onClick={() => { setChallenge(""); setCode(""); setError(""); }}>Назад</button>
            </form>
          ) : (
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
          )}
        </div>
      </main>
      <LoginFooter info={info} />
      <CookieNotice />
    </div>
  );
}
