import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError } from "../api";

/** Переход по ссылке `/p/<код>`: комната есть — входим; нет — создаётся (если администратор разрешил), иначе понятное сообщение. */
export default function PeerOpenPage() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    api.peer.open(code).then((r) => { if (alive) navigate(`/rooms/${r.slug}`, { replace: true }); })
      .catch((e) => { if (alive) setErr((e as ApiError).status === 404 ? "Комната не найдена или недоступна. Ссылка могла устареть, комната — закрыться, а создание комнат по ссылке — быть выключено." : (e as ApiError).message); });
    return () => { alive = false; };
  }, [code, navigate]);
  return (
    <section className="prejoin card" aria-live="polite">
      {err ? <><h1>Не удалось открыть комнату</h1><p className="muted">{err}</p><p><Link className="btn" to="/">К списку переговорок</Link></p></> : <p className="muted">Открываем комнату…</p>}
    </section>
  );
}
