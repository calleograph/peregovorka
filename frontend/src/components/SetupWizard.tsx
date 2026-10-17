import { useEffect, useState } from "react";
import { api, type SetupStatus } from "../api";
import { fmt } from "../util";
import { Modal } from "./Dialogs";

export const CHECK_LABEL: Record<string, string> = { ok: "работает", warn: "внимание", fail: "сбой", todo: "не настроено" };
export const CHECK_BADGE: Record<string, string> = { ok: "ok", warn: "warn", fail: "err", todo: "" };

const HINT: Record<string, string> = {
  ldap: "Адрес контроллера домена, Base DN и сервисная учётная запись для чтения. Вход сотрудников — только по защищённому соединению.",
  ca: "Корневой сертификат вашего удостоверяющего центра — чтобы проверять подлинность контроллеров домена (и почтового сервера).",
  access: "Какие группы каталога получают права администратора. Локальный администратор останется на случай сбоя каталога.",
  login_acl: "Какие группы каталога вообще могут входить в систему. Пока список пуст, входит любой активный пользователь каталога — лучше указать нужные группы.",
  storage: "Файловый сервер (SMB или каталог) для записей, протоколов и вложений чата — адрес и пароль вводятся один раз.",
  mail: "SMTP-сервер для отправки материалов встреч. Пароль не показывается после сохранения.",
  check: "Итоговая проверка: состояние системы и подключений.",
};

/**
 * Мастер первоначальной настройки после первого локального входа: LDAP → CA → группы администраторов → хранилище → почта → проверка.
 * Каждый шаг можно пропустить; после завершения (или «Закрыть и не показывать») мастер больше автоматически не появляется.
 */
export default function SetupWizard({ onGo, onClose, reopened = false }: { onGo: (page: string) => void; onClose: () => void; reopened?: boolean }) {
  const [st, setSt] = useState<SetupStatus | null>(null);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { void api.admin.setupStatus().then(setSt).catch(() => undefined); }, []);
  if (!st) return null;
  const finish = async () => {
    if (reopened) { onClose(); return; }                                   // повторное открытие из «Обзора»: настройка уже завершена, состояние не меняем
    setBusy(true); try { await api.admin.setupComplete(skipped); } catch { /* повторится при следующем входе */ } setBusy(false); onClose();
  };
  const recheck = async () => { setBusy(true); try { setSt(await api.admin.setupCheck()); } catch { /* страница покажет прежний результат */ } setBusy(false); };
  const doneCount = st.steps.filter((s) => s.done).length;
  return (
    <Modal title="Первоначальная настройка" onClose={() => void finish()}>
      <p className="muted" style={{ marginTop: 0 }}>{reopened ? "Что настроено и что работает на сервере. Выполненные настройки сохранены; проверки можно запустить заново." : "Вы вошли локальным администратором. Несколько шагов — и система готова к работе. Любой шаг можно пропустить и вернуться к нему позже в разделе «Администрирование»."}</p>
      <ol className="wizard-steps">
        {st.steps.map((s, i) => (
          <li key={s.id} className={s.done ? "done" : skipped.includes(s.id) ? "skipped" : ""}>
            <span className="wz-n" aria-hidden>{s.done ? "✓" : i + 1}</span>
            <div className="wz-body"><b>{s.title}</b>{s.done && <span className="badge ok">готово</span>}{!s.done && skipped.includes(s.id) && <span className="badge">пропущено</span>}
              <div className="muted small">{HINT[s.id]}</div></div>
            <div className="row tight">
              <button className="btn mini primary" onClick={() => { onGo(s.page); void finish(); }}>{s.done ? "Открыть" : "Настроить"}</button>
              {!reopened && !s.done && !skipped.includes(s.id) && <button className="btn mini ghost" onClick={() => setSkipped((x) => [...x, s.id])}>Пропустить</button>}
            </div>
          </li>
        ))}
      </ol>
      <h3 style={{ marginBottom: 6 }}>Проверка состояния сервера</h3>
      <p className="muted small" style={{ marginTop: 0 }}>{st.last_check_at ? `Последняя проверка: ${fmt(st.last_check_at)}. ` : "Проверка ещё не выполнялась. "}Проверяется с самого сервера: доступность с устройств пользователей уточните отдельно.</p>
      {st.checks.length > 0 && (
        <ul className="wizard-checks">
          {st.checks.map((c) => (
            <li key={c.id}>
              <span className={`badge ${CHECK_BADGE[c.status] ?? ""}`}>{CHECK_LABEL[c.status] ?? c.status}</span>
              <div className="wz-body"><b>{c.title}</b><div className="muted small">{c.detail}</div></div>
              {c.status !== "ok" && <button className="btn mini" onClick={() => { onGo(c.page); void finish(); }}>Открыть</button>}
            </li>
          ))}
        </ul>
      )}
      <div className="row"><span className="muted small">Настроено шагов: {doneCount} из {st.steps.length}</span><div className="spacer" />
        <button className="btn" disabled={busy} onClick={() => void recheck()}>{busy ? "Проверяем…" : "Проверить повторно"}</button>
        <button className="btn primary" disabled={busy} onClick={() => void finish()}>{reopened ? "Закрыть" : "Закрыть и не показывать снова"}</button></div>
    </Modal>
  );
}
