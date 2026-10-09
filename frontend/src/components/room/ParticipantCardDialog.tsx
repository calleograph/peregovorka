import { useEffect, useState } from "react";
import { api, type ApiError, type ParticipantCard } from "../../api";
import Avatar from "../Avatar";
import { Modal } from "../Dialogs";

/** Карточка участника: аватарка, ФИО, должность, подразделение и разрешённые контакты. Состав полей ограничен сервером (белый список); гостю и о госте — только имя. */
export default function ParticipantCardDialog({ meetingId, identity, name, role, onClose }: { meetingId: string; identity: string; name: string; role?: string; onClose: () => void }) {
  const [card, setCard] = useState<ParticipantCard | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { void api.participantCard(meetingId, identity).then(setCard).catch((e) => setErr((e as ApiError).message)); }, [meetingId, identity]);
  const full = card?.card === "full";
  return (
    <Modal title="Участник" onClose={onClose}>
      <div className="pcard">
        <Avatar name={card?.name ?? name} url={card?.avatar_url} size={84} />
        <div className="pcard-main">
          <h3>{card?.name ?? name}</h3>
          {role && <span className="badge">{role}</span>}
          {card?.guest && <span className="badge">Гость</span>}
          {full && card?.title && <p className="pcard-title">{card.title}</p>}
          {full && card?.department && <p className="muted">{card.department}</p>}
        </div>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {full && (card?.email || card?.phone || card?.login) && (
        <dl className="profile-dl">
          {card?.email && <div><dt>E-mail</dt><dd><a href={`mailto:${card.email}`}>{card.email}</a></dd></div>}
          {card?.phone && <div><dt>Телефон</dt><dd>{card.phone}</dd></div>}
          {card?.login && <div><dt>Логин</dt><dd>{card.login}</dd></div>}
        </dl>
      )}
      {card && !full && !card.guest && <p className="muted small">Подробные данные участников доступны только сотрудникам.</p>}
      {full && !card?.title && !card?.department && !card?.email && !card?.phone && <p className="muted small">Дополнительных данных в профиле нет.</p>}
      <div className="row"><button className="btn ghost" onClick={onClose}>Закрыть</button></div>
    </Modal>
  );
}
