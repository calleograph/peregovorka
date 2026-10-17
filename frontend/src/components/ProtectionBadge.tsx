import { useState } from "react";
import { protectionOf, type ProtectionInput } from "../protection";
import { Modal } from "./Dialogs";

/** Простая схема «ящики и стрелки»: подписи — человеческим языком, цвета — из темы. */
function Flow({ steps, title }: { steps: { label: string; note?: string; lock?: boolean }[]; title: string }) {
  const w = 150, gap = 54, h = 86;
  const total = steps.length * w + (steps.length - 1) * gap;
  return (
    <figure className="prot-flow">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${total} ${h + 16}`} role="img" aria-label={`${title}: ${steps.map((s) => s.label).join(" → ")}`} style={{ width: "100%", maxWidth: total, height: "auto" }}>
        {steps.map((s, i) => {
          const x = i * (w + gap);
          return (
            <g key={s.label}>
              <rect x={x} y={8} width={w} height={h - 8} rx={10} className={s.lock ? "pf-box pf-lock" : "pf-box"} />
              <text x={x + w / 2} y={36} textAnchor="middle" className="pf-title">{s.label}</text>
              {s.note && <text x={x + w / 2} y={58} textAnchor="middle" className="pf-note">{s.note}</text>}
              {i < steps.length - 1 && (
                <g className="pf-arrow">
                  <line x1={x + w + 6} y1={h / 2 + 4} x2={x + w + gap - 10} y2={h / 2 + 4} />
                  <polygon points={`${x + w + gap - 10},${h / 2 - 3} ${x + w + gap - 2},${h / 2 + 4} ${x + w + gap - 10},${h / 2 + 11}`} />
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

/** Индикатор защиты в шапке комнаты: красная надпись при включённом шифровании, рядом ⓘ с пояснением и схемами. */
export default function ProtectionBadge({ input }: { input: ProtectionInput }) {
  const [open, setOpen] = useState(false);
  const p = protectionOf(input);
  return (
    <>
      <span className={`prot-badge prot-${p.tone}`} title={`Уровень защиты: ${p.kind}`}>
        <span className="prot-label">{p.label}</span>
        {p.tone === "red" && <span className="prot-kind">{p.kind}</span>}
        <button type="button" className="prot-info" onClick={() => setOpen(true)} aria-label="Что именно защищено: пояснение и схема" title="Что именно защищено">ⓘ</button>
      </span>
      {open && (
        <Modal title="Защита этой встречи" onClose={() => setOpen(false)}>
          <div className="prot-body">
            <ul className="prot-rows">
              {p.rows.map((r) => (
                <li key={r.title} className={r.ok ? "ok" : "off"}><b>{r.ok ? "✓" : "—"} {r.title}</b><br /><span>{r.text}</span></li>
              ))}
            </ul>
            {input.e2ee && <Flow title="Звук и видео: сквозное шифрование" steps={[
              { label: "Участник А", note: "шифрует на устройстве", lock: true }, { label: "Сервер LiveKit", note: "видит только шифртекст" }, { label: "Участник Б", note: "расшифровывает у себя", lock: true }]} />}
            <Flow title="Чат и стенограммы: защита при хранении" steps={[
              { label: "Пользователь", note: "ваш браузер" }, { label: "Сервер", note: input.https ? "соединение по TLS" : "без TLS" }, { label: "Шифрование", note: input.storage ? "AES-256-GCM" : "выключено", lock: input.storage }, { label: "Хранилище", note: input.storage ? "лежит зашифрованным" : "открытым текстом" }]} />
            <h4>Кто имеет доступ</h4>
            <ul className="small">
              <li><b>Участники встречи</b> видят чат и стенограмму (при хранении шифруется только то, что лежит в базе).</li>
              <li><b>Сервер Peregovorka</b> расшифровывает записи в памяти, чтобы показать их участникам и сформировать протокол. Администратор сервера с доступом к процессу и мастер-ключу сможет прочитать содержимое: это защита от утечки базы и её копий, а не от владельца сервера.</li>
              <li><b>Ключи.</b> Ключи данных хранятся в базе только в зашифрованном виде; расшифровывает их мастер-ключ сервера (<code>APP_MASTER_KEY</code>), которого в базе нет. Его копию нужно хранить отдельно: без него зашифрованные записи не прочитать.</li>
              <li><b>Запись и транскрибация.</b> {input.e2ee ? (input.e2eeRecorder ? "Выполняются доверенным компонентом сервера, получающим ключ комнаты." : "Для этой комнаты отключены: сервер не имеет ключа к звуку.") : "Работают как обычно: сервер получает звук, распознаёт речь и записывает результат; запись звука хранится в файловом хранилище по настройкам комнаты и этим шифрованием не затрагивается."}</li>
              <li><b>Что защищается при хранении:</b> сообщения чата и стенограммы {input.storage ? "(включено)" : "(для этой комнаты выключено)"}. Протоколы, файлы и записи звука этим режимом не шифруются.</li>
            </ul>
          </div>
        </Modal>
      )}
    </>
  );
}
