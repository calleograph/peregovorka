import type { ReactNode } from "react";
import type { DiagResult } from "../../api";

const STAGE_TITLE: Record<string, string> = {
  config: "Настройка", dns: "DNS", tcp: "TCP-соединение", connect: "Подключение", tls: "Защита (TLS)", certificate: "Сертификат", bind: "Вход сервисной записи",
  base_dn: "Чтение Base DN", auth: "Вход", sender_rejected: "Адрес отправителя", recipient_rejected: "Адрес получателя", data: "Отправка", smtp: "Ответ сервера",
};

/** Результат пошаговой проверки подключения (LDAPS, SMTP): каждый этап — отдельной строкой с понятной причиной. */
export function StageList({ result }: { result: DiagResult }) {
  return (
    <div className={`alert ${result.ok ? "ok" : "error"}`} role="status">
      <strong>{result.ok ? "✓ Проверка пройдена" : "✗ Проверка не пройдена"}</strong>
      <ul className="stage-list">
        {result.stages.map((s, i) => (
          <li key={i} className={s.ok === false ? "bad" : s.ok === null ? "skip" : "ok"}>
            <span className="stage-mark" aria-hidden>{s.ok === false ? "✗" : s.ok === null ? "•" : "✓"}</span>
            <span><b>{STAGE_TITLE[s.stage] ?? s.stage}.</b> {s.message}{s.ms ? <span className="muted small"> ({s.ms} мс)</span> : null}</span>
          </li>
        ))}
        {!result.ok && result.stages.length === 0 && result.message && <li className="bad"><span>{result.message}</span></li>}
      </ul>
    </div>
  );
}

/**
 * Секрет (пароль): сохранённое значение НИКОГДА не показывается — только «задан». Чтобы заменить, нажмите «Изменить»; пустое поле при сохранении
 * означает «не менять». Если `onClear` передан, есть и «Удалить сохранённый пароль».
 */
export function SecretInput({ label, isSet, value, onChange, onClear, help, placeholder, disabled }: {
  label: string; isSet: boolean; value: string; onChange: (v: string) => void; onClear?: () => void; help?: ReactNode; placeholder?: string; disabled?: boolean;
}) {
  const editing = !isSet || value !== "";
  return (
    <label>{label}
      {isSet && !editing ? (
        <span className="row tight secret-row">
          <span className="badge ok">пароль задан</span>
          <button type="button" className="btn mini" onClick={() => onChange(" ")} disabled={disabled}>Изменить</button>
          {onClear && <button type="button" className="btn mini danger" onClick={onClear} disabled={disabled}>Удалить сохранённый пароль</button>}
        </span>
      ) : (
        <span className="row tight secret-row">
          <input type="password" autoComplete="new-password" value={value === " " ? "" : value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder ?? (isSet ? "новый пароль" : "")} disabled={disabled} autoFocus={isSet} />
          {isSet && <button type="button" className="btn mini" onClick={() => onChange("")}>Отмена</button>}
        </span>
      )}
      {help && <span className="help">{help}</span>}
    </label>
  );
}

/** Новое значение секрета для сохранения: undefined — не менять. */
export const secretToSend = (v: string): string | undefined => (v === "" || v === " " ? undefined : v);

export const fmtDate = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—");
