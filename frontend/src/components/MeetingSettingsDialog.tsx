import { useEffect, useState } from "react";
import { api, type ApiError, type LlmChoice, type MeetingSettings } from "../api";
import { emptyChoice } from "../phone";
import { Modal } from "./Dialogs";
import LlmChoiceEditor from "./LlmChoiceEditor";

/**
 * «Эта встреча»: языковая модель только для идущей встречи (рассылка материалов с 0.64 — только в настройках комнаты). Значения по умолчанию берутся из настроек комнаты; здесь руководитель
 * может изменить их для этой встречи, не трогая комнату, и в любой момент вернуть «как в комнате».
 */
export default function MeetingSettingsDialog({ meetingId, onClose }: { meetingId: string; roomId?: string; onClose: () => void }) {
  const [st, setSt] = useState<MeetingSettings | null>(null);
  const [tab, setTab] = useState<"delivery" | "model">("model");
  const [choice, setChoice] = useState<LlmChoice>(emptyChoice());
  const [choiceSummary, setChoiceSummary] = useState<LlmChoice>(emptyChoice());
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = (s: MeetingSettings) => { setSt(s); setChoice(s.llm.override ?? { mode: "inherit", profile_id: null }); setChoiceSummary(s.llm_summary.override ?? { mode: "inherit", profile_id: null }); };
  useEffect(() => { void api.manage.meetingSettings(meetingId).then(apply).catch((e) => setErr((e as ApiError).message)); }, [meetingId]);

  const save = async (what: "llm", reset = false) => {
    setBusy(true); setErr(""); setNote("");
    try {
      const one = (c: LlmChoice) => (reset || c.mode === "inherit" ? null : c);
      const body = what === "llm" ? { llm: one(choice), llm_summary: one(choiceSummary) } : {};
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
          <p className="help" style={{ marginTop: 0 }}>Здесь можно изменить языковую модель <b>только для этой встречи</b>. Настройки комнаты не меняются и снова действуют на следующей встрече.</p>
          <div className="tabs" role="tablist" aria-label="Настройки встречи">
            <button type="button" role="tab" aria-selected={tab === "delivery"} className={`tab ${tab === "delivery" ? "active" : ""}`} onClick={() => setTab("delivery")}>Рассылка материалов</button>
            <button type="button" role="tab" aria-selected={tab === "model"} className={`tab ${tab === "model" ? "active" : ""}`} onClick={() => setTab("model")}>Языковая модель{st.llm.override || st.llm_summary.override ? " ●" : ""}</button>
          </div>
          {tab === "delivery" && (
            <div className="alert info">Автоматическая рассылка материалов настраивается только в настройках комнаты (по умолчанию выключена) и действует одинаково для всех её встреч.
              Отправить материалы этой встречи вручную — кому угодно, в том числе гостям, оставившим почту, — можно на странице встречи в «Истории» после её завершения.</div>
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
