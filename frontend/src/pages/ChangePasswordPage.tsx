import { FormEvent, useState } from "react";
import { api, ApiError } from "../api";

/** Первичный (или сброшенный) пароль локального администратора нужно заменить на свой до начала работы. */
export default function ChangePasswordPage({ onDone, onLogout }: { onDone: () => void; onLogout: () => void }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const mismatch = again !== "" && again !== next;
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try { await api.changePassword(cur, next); onDone(); }
    catch (err) { setError((err as ApiError).message || "Не удалось сменить пароль"); setBusy(false); }
  };
  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={submit}>
        <h1>Задайте свой пароль</h1>
        <p className="muted">Вы вошли с первичным паролем из терминала установки. Замените его на свой — первичный пароль после этого перестанет работать.</p>
        <label>Первичный пароль<input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" autoFocus required /></label>
        <label>Новый пароль<input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" required minLength={12} />
          <span className="help">Не короче 12 символов, не менее двух видов символов (буквы разного регистра, цифры, знаки), без имени пользователя.</span></label>
        <label>Ещё раз<input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" required aria-invalid={mismatch} />
          {mismatch && <span className="field-err" role="alert">Пароли не совпадают</span>}</label>
        {error && <div className="alert error" role="alert">{error}</div>}
        <div className="row"><button className="btn primary" disabled={busy || !cur || next.length < 12 || mismatch || !again}>{busy ? "Сохранение…" : "Сменить пароль"}</button>
          <button type="button" className="btn ghost" onClick={onLogout}>Выйти</button></div>
      </form>
    </div>
  );
}
