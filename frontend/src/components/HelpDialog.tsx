import { Link } from "react-router-dom";
import { Modal } from "./Dialogs";
import { useSite } from "../site";

/** «Помощь и поддержка»: контакты и инструкции организации. Адрес поддержки — только для связи с людьми; он не связан с адресом отправителя почты. Пустые поля не показываются. */
export default function HelpDialog({ onClose }: { onClose: () => void }) {
  const s = useSite();
  const sp = s.support;
  const docs = s.documents;
  return (
    <Modal title="Помощь и поддержка" onClose={onClose}>
      {!sp && !docs.length && <p className="muted">Организация пока не указала контакты поддержки.</p>}
      {sp?.support_text && <p style={{ whiteSpace: "pre-wrap" }}>{sp.support_text}</p>}
      {sp && (
        <dl className="profile-dl">
          {sp.support_email && <div><dt>Электронная почта</dt><dd><a href={`mailto:${sp.support_email}`}>{sp.support_email}</a></dd></div>}
          {sp.support_phone && <div><dt>Телефон</dt><dd><a href={`tel:${sp.support_phone.replace(/[^0-9+]/g, "")}`}>{sp.support_phone}</a></dd></div>}
          {sp.support_url && <div><dt>Заявки и обращения</dt><dd><a href={sp.support_url} target="_blank" rel="noopener noreferrer">{sp.support_url}</a></dd></div>}
          {sp.portal_url && <div><dt>Корпоративный портал</dt><dd><a href={sp.portal_url} target="_blank" rel="noopener noreferrer">{sp.portal_url}</a></dd></div>}
        </dl>
      )}
      {docs.length > 0 && (
        <>
          <h3>Документы</h3>
          <ul className="legal-links">{docs.map((d) => <li key={d.kind}><Link to={`/legal/${d.kind}`} onClick={onClose}>{d.title}</Link></li>)}</ul>
        </>
      )}
      <div className="row"><button className="btn ghost" onClick={onClose}>Закрыть</button></div>
    </Modal>
  );
}
