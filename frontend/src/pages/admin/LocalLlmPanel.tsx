import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type LocalLlmStatus } from "../../api";
import { bytes } from "../../util";

const FILE_TEXT: Record<LocalLlmStatus["file"]["state"], string> = {
  ok: "на месте, размер и контрольная сумма проверены",
  missing: "не загружена",
  partial: "загрузка не завершена (файл неполный)",
  bad_size: "файл повреждён: размер не совпадает с ожидаемым",
  bad_hash: "файл повреждён или изменён: контрольная сумма не совпадает",
};
const MODE_TEXT = { local: "Локальная (встроенная)", external: "Внешняя LLM", off: "Отключено" } as const;

/**
 * Встроенная локальная языковая модель (Qwen3 1.7B Q4_K_M, llama.cpp, CPU): состояние файла и runtime, проверка и загрузка. Файл хранится вне образов
 * в каталоге моделей, при обновлении проекта не скачивается заново; скачивает и перезагружает его помощник обновлений на сервере (кнопка ниже).
 */
export default function LocalLlmPanel() {
  const [st, setSt] = useState<LocalLlmStatus | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [downloading, setDownloading] = useState(false);

  const load = useCallback(async () => {
    try { setSt(await api.admin.localLlm()); setErr(""); } catch (e) { setErr((e as ApiError).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const t = window.setInterval(() => void load(), downloading ? 3000 : 30000);
    return () => window.clearInterval(t);
  }, [downloading, load]);
  // загрузка идёт на сервере: следим за результатом исправления «llm_model»
  useEffect(() => {
    if (!downloading) return;
    const t = window.setInterval(async () => {
      try {
        const r = await api.admin.repairs();
        const cur = r.current;
        if (cur && cur.repair_id === "llm_model" && cur.state === "idle" && cur.finished_at) {
          setDownloading(false);
          setMsg(cur.result === "ok" ? { ok: true, text: "Модель загружена и запущена." } : { ok: false, text: "Загрузка не удалась (нет доступа в интернет или к адресу модели). Журнал — «Обновления и версии». Остальная система работает." });
          void load();
        }
      } catch { /* повторим на следующем шаге */ }
    }, 3000);
    return () => window.clearInterval(t);
  }, [downloading, load]);

  const download = async () => {
    setMsg(null); setBusy("download");
    try { await api.admin.repairFix("llm_model"); setDownloading(true); setMsg({ ok: true, text: "Загрузка началась на сервере (~484 МБ, несколько минут). Страницу можно не закрывать." }); }
    catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    setBusy("");
  };
  const test = async () => {
    setMsg(null); setBusy("test");
    try { const r = await api.admin.localLlmTest(); setMsg({ ok: r.ok, text: r.ok ? `${r.message} (${r.ms} мс)` : r.message }); }
    catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    setBusy("");
  };

  if (err && !st) return <div className="alert error">{err}</div>;
  if (!st) return null;
  const f = st.file;
  const missing = f.state !== "ok";
  const badge = st.ready ? <span className="badge ok">работает</span> : !missing ? <span className="badge warn">{st.runtime.reachable ? "загружается" : "не запущена"}</span>
    : <span className="badge warn">{f.state === "missing" ? "не загружена" : "повреждена"}</span>;

  return (
    <section className="card local-llm" aria-label="Локальная языковая модель">
      <div className="row"><h3 style={{ margin: 0 }}>Встроенная локальная модель</h3><span className="badge">локальная</span>{badge}
        <div className="spacer" /><span className="muted small">Сейчас выбрано: <b>{MODE_TEXT[st.provider]}</b></span></div>
      {!st.enabled_by_install && <div className="alert info">Локальная модель отключена при установке (<code>LLM_LOCAL_ENABLED=no</code> в файле настроек сервера): она не загружается и не запускается.</div>}
      <table className="table compact"><tbody>
        <tr><td>Модель</td><td><b>{st.model.title}</b> — {st.model.runtime}, контекст {st.model.context_tokens.toLocaleString("ru-RU")} токенов</td></tr>
        <tr><td>Файл модели</td><td>{FILE_TEXT[f.state]}{f.size_bytes ? ` · ${bytes(f.size_bytes)}` : ""} <span className="muted small">(ожидается {bytes(f.expected_bytes)})</span></td></tr>
        <tr><td>Контрольная сумма SHA-256</td><td>{f.sha256_state === "ok" ? "✓ совпадает" : f.sha256_state === "mismatch" ? <span className="badge warn">не совпадает</span> : f.sha256_state === "skipped" ? "проверка отключена в настройках сервера" : "—"}</td></tr>
        <tr><td>Сервер модели</td><td>{missing ? "—" : st.runtime.detail}</td></tr>
        <tr><td>Где работает</td><td>{st.endpoint}. Данные встреч не отправляются наружу.</td></tr>
        <tr><td>Для каких задач</td><td>{st.model.tasks.join(", ")}</td></tr>
      </tbody></table>
      {st.model.light && <div className="alert info">{st.model.note} Для встреч длиннее ~{st.model.warn_input_chars.toLocaleString("ru-RU")} знаков стенограммы перед созданием протокола будет показано предупреждение.</div>}
      {st.provider === "local" && !st.ready && <div className="alert error">Выбран режим «Локальная», но модель не готова: протоколы и резюме не будут создаваться, пока она не загружена и не запущена.</div>}
      {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
      <div className="row">
        <button className="btn" onClick={() => void test()} disabled={!!busy || missing}>{busy === "test" ? "Проверка…" : "Проверить модель"}</button>
        {(missing || downloading) && (
          <button className="btn primary" onClick={() => void download()} disabled={!!busy || downloading || !st.enabled_by_install}
                  title="Загружает файл модели на сервер (нужен интернет), проверяет размер и контрольную сумму и запускает контейнер модели">
            {downloading ? "Загрузка…" : f.state === "missing" || f.state === "partial" ? "Скачать модель" : "Скачать заново (файл повреждён)"}</button>)}
        <span className="muted small">Файл хранится на сервере вне образов и при обновлении проекта заново не скачивается. Позже можно подключить более сильную локальную модель без смены интерфейса.</span>
      </div>
    </section>
  );
}
