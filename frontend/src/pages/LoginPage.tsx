import { type CSSProperties, FormEvent, KeyboardEvent, useState } from "react";
import { api, ApiError, type Me } from "../api";
import { Icon } from "../components/Icons";
import { magnet } from "../fx";
import { isValidLogin, LOGIN_HINT } from "../loginRules";

const FEATURES: [Parameters<typeof Icon>[0]["name"], string, string][] = [
  ["transcript", "Стенограмма по ходу встречи", "Речь распознаётся на вашем сервере и сразу попадает в текст — без внешних сервисов."],
  ["sparkle", "Протокол и резюме", "Встроенная локальная модель собирает решения, поручения и сроки; у каждого пункта есть реплика-источник."],
  ["shield", "Данные остаются у вас", "Записи, стенограммы и протоколы хранятся на вашем сервере и доступны по правилам вашей организации."],
];

export default function LoginPage({ onLogin, version }: { onLogin: (m: Me) => void; version: string }) {
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
    <div className="login-page">
      <aside className="login-brand" aria-hidden={false}>
        <div className="orb o1" aria-hidden /><div className="orb o2" aria-hidden /><div className="orb o3" aria-hidden />
        <div className="brand-inner">
          <div className="logo"><span className="logo-mark" aria-hidden><Icon name="video" size={20} /></span> Peregovorka</div>
          <h2>Видеовстречи, которые сами превращаются в протокол</h2>
          <ul className="feat">
            {FEATURES.map(([icon, title, text], i) => (
              <li key={title} style={{ "--i": i } as CSSProperties}><span className="fi" aria-hidden><Icon name={icon} size={18} /></span><div><b>{title}</b><p>{text}</p></div></li>
            ))}
          </ul>
        </div>
      </aside>
      <main className="login-side">
        <form className="login-card" onSubmit={submit}>
          <h1>Вход в Peregovorka</h1>
          <p className="muted">Используйте доменную учётную запись. Администратор сервера при первой настройке входит локальной учётной записью.</p>
          <label>Логин
            <input value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" autoFocus required maxLength={256}
                   autoCapitalize="none" spellCheck={false} aria-invalid={login !== "" && !valid} placeholder="user1  или  user1@example.local" />
            {login !== "" && !valid && <span className="field-err" role="alert">{LOGIN_HINT}</span>}
          </label>
          <label>Пароль
            <span className="pw">
              <input type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} onKeyUp={checkCaps} onKeyDown={checkCaps}
                     autoComplete="current-password" required maxLength={512} />
              <button type="button" className="pw-toggle" onClick={() => setShow((s) => !s)} aria-label={show ? "Скрыть пароль" : "Показать пароль"} aria-pressed={show}>
                <Icon name={show ? "eyeOff" : "eye"} size={18} />
              </button>
            </span>
            {caps && <span className="field-warn" role="status">Включён Caps Lock</span>}
          </label>
          {error && <div className="alert error" role="alert">{error}</div>}
          <button className="btn primary cta wide" {...magnet} disabled={busy || !valid || !password}>
            {busy ? <><i className="spin" aria-hidden /> Проверка…</> : <>Войти <Icon name="arrowR" size={17} /></>}
          </button>
          {version && <div className="muted small ver">Версия {version}</div>}
        </form>
      </main>
    </div>
  );
}
