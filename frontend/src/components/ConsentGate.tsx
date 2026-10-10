import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError } from "../api";
import { Brand } from "./Brand";

/** Подтверждение документов организации после входа: показывается, пока пользователь явно не подтвердит действующие редакции документов с включённым параметром
 *  «Требовать подтверждение при входе». Простое посещение сайта согласием не считается; ссылки открывают актуальный текст. */
export default function ConsentGate({ items, onDone, onLogout }: { items: { kind: string; title: string; version: number }[]; onDone: () => void; onLogout: () => void }) {
  const [on, setOn] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const all = on.size === items.length;
  const send = async () => {
    setBusy(true); setErr("");
    try { await api.legal.consent(items.map((i) => i.kind)); onDone(); }
    catch (e) { setErr((e as ApiError).message); }
    finally { setBusy(false); }
  };
  return (
    <main className="center-card">
      <section className="card prejoin" role="dialog" aria-modal="true" aria-label="Подтверждение документов">
        <h1><Brand name /></h1>
        <p>Перед началом работы подтвердите, что вы ознакомились с документами организации.</p>
        {items.map((i) => (
          <label key={i.kind} className="check">
            <input type="checkbox" checked={on.has(i.kind)} onChange={(e) => setOn((s) => { const n = new Set(s); if (e.target.checked) n.add(i.kind); else n.delete(i.kind); return n; })} />
            <span className="check-body">Я ознакомился(лась) с документом «<Link to={`/legal/${i.kind}`} target="_blank" rel="noopener noreferrer">{i.title}</Link>» (редакция {i.version})</span>
          </label>
        ))}
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row">
          <button className="btn primary" disabled={!all || busy} onClick={() => void send()}>{busy ? "Сохраняем…" : "Подтверждаю"}</button>
          <button className="btn ghost" onClick={onLogout}>Выйти</button>
        </div>
      </section>
    </main>
  );
}
