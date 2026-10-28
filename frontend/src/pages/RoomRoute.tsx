import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type ApiError, type RoomRef } from "../api";
import RoomPage from "./RoomPage";
import BroadcastChooser from "../components/BroadcastChooser";

/**
 * Комната по адресу /rooms/<технический идентификатор>. Внутри системы комната по-прежнему определяется UUID: сначала адрес (или UUID из старой ссылки,
 * или прежний адрес после переименования) переводится в комнату, затем в строке браузера остаётся нынешний адрес.
 */
export default function RoomRoute({ selfName }: { selfName?: string }) {
  const { roomId: ref = "" } = useParams();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
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
  // трансляционная комната: сначала роль — «Выступающий» (обычный вход, ссылка с ?as=speaker сразу ведёт сюда) или «Зритель» (лёгкая страница просмотра)
  if (room.room?.room_type === "broadcast" && room.room.live_enabled && sp.get("as") !== "speaker") {
    return <BroadcastChooser name={room.name} live={!!room.room.live} viewers={room.room.live_viewers ?? 0} watch={room.live_watch ?? null} hasPassword={!!room.room.has_password}
      onSpeaker={() => setSp({ as: "speaker" }, { replace: true })} onViewer={() => room.live_watch && navigate(room.live_watch)} />;
  }
  return <RoomPage key={room.id} roomIdOverride={room.id} roomInfo={room.room} selfName={selfName} />;
}
