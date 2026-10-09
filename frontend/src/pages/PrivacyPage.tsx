import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { loadPrivacy, type Privacy } from "../privacy";

const SECTIONS: [keyof Privacy, string][] = [["operator", "Оператор"], ["purpose", "Назначение системы"], ["data_types", "Какие данные обрабатываются"],
  ["cookies", "Технические cookie"], ["retention", "Сроки хранения"], ["contact", "Вопросы и обращения"]];

/** «Обработка данных»: страница открывается без входа. Тексты вводит администратор («Администрирование → Конфиденциальность и cookie»); в приложении юридический текст не зашит. */
export default function PrivacyPage() {
  const [p, setP] = useState<Privacy | null>(null);
  useEffect(() => { void loadPrivacy().then(setP); }, []);
  const filled = p ? SECTIONS.filter(([k]) => String(p[k] ?? "").trim()) : [];
  return (
    <main className="privacy-page">
      <p><Link to="/">← На главную</Link></p>
      <h1>Обработка данных</h1>
      {!p && <p className="muted">Загрузка…</p>}
      {p && filled.length === 0 && !p.policy_url && <p className="muted">Администратор ещё не разместил здесь сведения. По вопросам обратитесь в службу поддержки вашей организации.</p>}
      {filled.map(([k, title]) => (
        <section key={k} className="card privacy-block"><h2>{title}</h2><p style={{ whiteSpace: "pre-wrap" }}>{String(p![k])}</p></section>))}
      {p?.policy_url && <p><a href={p.policy_url} target="_blank" rel="noopener noreferrer">Внутренняя политика обработки данных</a></p>}
    </main>
  );
}
