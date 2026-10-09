import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError, type RoomRef } from "../api";
import RoomPage from "./RoomPage";

/**
 * Комната по адресу /rooms/<технический идентификатор>. Внутри системы комната по-прежнему определяется UUID: сначала адрес (или UUID из старой ссылки,
 * или прежний адрес после переименования) переводится в комнату, затем в строке браузера остаётся нынешний адрес.
 */
export default function RoomRoute({ selfName }: { selfName?: string }) {
  const { roomId: ref = "" } = useParams();
  const navigate = useNavigate();
  const [room, setRoom] = useState<RoomRef | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setError("");
    api.resolveRoom(ref).then((r) => {
      if (!alive) return;
      setRoom(r);
      if (!r.canonical) navigate(`/rooms/${r.slug}`, { replace: true });
    }).catch((e) => { if (alive) setError((e as ApiError).message); });
    return () => { alive = false; };
  }, [ref, navigate]);

  if (error) return <div className="alert error">{error} <Link to="/">К списку переговорок</Link></div>;
  if (!room) return <div className="muted">Загрузка…</div>;
  if (room.lifecycle === "closed") {
    return <div className="card"><h2>{room.name}</h2><p>Временная переговорка закрыта: новую встречу в ней начать нельзя. Запись, стенограмма, чат и протоколы остались в <Link to="/history">«Истории»</Link>.</p></div>;
  }
  return <RoomPage key={room.id} roomIdOverride={room.id} roomInfo={room.room} selfName={selfName} />;
}
