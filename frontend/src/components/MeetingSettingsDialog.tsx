import { useEffect, useState } from "react";
import { api, type ApiError, type LlmChoice, type MailDeliverySpec, type MeetingSettings } from "../api";
import { emptyChoice } from "../phone";
import DeliveryEditor, { emptySpec } from "./DeliveryEditor";
import { Modal } from "./Dialogs";
import LlmChoiceEditor from "./LlmChoiceEditor";

/**
 * «Эта встреча»: рассылка материалов и языковая модель только для идущей встречи. Значения по умолчанию берутся из настроек комнаты; здесь руководитель
 * может изменить их для этой встречи, не трогая комнату, и в любой момент вернуть «как в комнате».
 */
export default function MeetingSettingsDialog({ meetingId, roomId, onClose }: { meetingId: string; roomId: string; onClose: () => void }) {
  const [st, setSt] = useState<MeetingSettings | null>(null);
  const [tab, setTab] = useState<"delivery" | "model">("delivery");
  const [spec, setSpec] = useState<MailDeliverySpec>(emptySpec());
  const [choice, setChoice] = useState<LlmChoice>(emptyChoice());
  const [choiceSummary, setChoiceSummary] = useState<LlmChoice>(emptyChoice());
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = (s: MeetingSettings) => { setSt(s); setSpec(s.delivery.effective); setChoice(s.llm.override ?? { mode: "inherit", profile_id: null, local_model: null }); setChoiceSummary(s.llm_summary.override ?? { mode: "inherit", profile_id: null, local_model: null }); };
  useEffect(() => { void api.manage.meetingSettings(meetingId).then(apply).catch((e) => setErr((e as ApiError).message)); }, [meetingId]);

  const save = async (what: "delivery" | "llm", reset = false) => {
    setBusy(true); setErr(""); setNote("");
    try {
      const one = (c: LlmChoice) => (reset || c.mode === "inherit" ? null : c);
      const body = what === "delivery" ? { delivery: reset ? null : spec } : { llm: one(choice), llm_summary: one(choiceSummary) };
      apply(await api.manage.saveMeetingSettings(meetingId, body));
      setNote(reset ? "Возвращено «как в комнате»." : "Сохранено для этой встречи.");
    } catch (e) { setErr((e as ApiError).message); }
    setBusy(false);
  };

  return (
    <Modal title="Настройки этой встречи" onClose={onClose} wide>
      {!st && !err && <p className="muted">Загрузка…</p>}
      {st && (
        <div className="form room-form">
          <p className="help" style={{ marginTop: 0 }}>Здесь можно изменить рассылку материалов и языковую модель <b>только для этой встречи</b>. Настройки комнаты не меняются и снова действуют на следующей встрече.</p>
          <div className="tabs" role="tablist" aria-label="Настройки встречи">
            <button type="button" role="tab" aria-selected={tab === "delivery"} className={`tab ${tab === "delivery" ? "active" : ""}`} onClick={() => setTab("delivery")}>Рассылка материалов{st.delivery.override ? " ●" : ""}</button>
            <button type="button" role="tab" aria-selected={tab === "model"} className={`tab ${tab === "model" ? "active" : ""}`} onClick={() => setTab("model")}>Языковая модель{st.llm.override || st.llm_summary.override ? " ●" : ""}</button>
          </div>
          {tab === "delivery" && (
            <>
              {st.ended
                ? <div className="alert info">Встреча уже завершена — рассылка для неё определена. Отправить материалы вручную можно на странице встречи в истории.</div>
                : <>
                  <p className="muted small">{st.delivery.override ? "Для этой встречи действуют свои настройки (● — изменено)." : "Сейчас действуют настройки комнаты."} После завершения встречи письма уйдут по этим настройкам.</p>
                  <DeliveryEditor roomId={roomId} spec={spec} onChange={setSpec} />
                  <div className="row form-actions">
                    <button type="button" className="btn primary" disabled={busy} onClick={() => void save("delivery")}>Сохранить для этой встречи</button>
                    <button type="button" className="btn" disabled={busy || !st.delivery.override} onClick={() => void save("delivery", true)}>Вернуть как в комнате</button>
                  </div></>}
            </>
          )}
          {tab === "model" && (
            <>
              <h3 style={{ margin: "0 0 6px" }}>Модель для протокола</h3>
              <LlmChoiceEditor scope="meeting" purpose="protocol" value={choice} onChange={setChoice} options={st.llm_options} effective={st.llm.effective} />
              <h3 style={{ margin: "18px 0 6px" }}>Модель для резюме</h3>
              <LlmChoiceEditor scope="meeting" purpose="summary" value={choiceSummary} onChange={setChoiceSummary} options={st.llm_options} effective={st.llm_summary.effective} />
              <div className="row form-actions">
                <button type="button" className="btn primary" disabled={busy} onClick={() => void save("llm")}>Сохранить для этой встречи</button>
                <button type="button" className="btn" disabled={busy || (!st.llm.override && !st.llm_summary.override)} onClick={() => void save("llm", true)}>Вернуть как в комнате</button>
              </div>
            </>
          )}
        </div>
      )}
      {note && <div className="alert ok" role="status">{note}</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="row form-actions"><button type="button" className="btn ghost" onClick={onClose}>Закрыть</button></div>
    </Modal>
  );
}
