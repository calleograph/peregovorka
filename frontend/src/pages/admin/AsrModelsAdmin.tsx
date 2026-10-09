import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type AsrCompare, type AsrModel, type AsrModels, type AsrTestResult } from "../../api";
import { bytes } from "../../util";
import { vadFields } from "./fields";
import SettingsForm from "./SettingsForm";

const RUNTIME_LABEL: Record<string, string> = { pytorch: "PyTorch", gguf: "GGUF · transcribe.cpp", onnx: "ONNX" };
const STATUS: Record<string, [string, string]> = {
  active: ["Активна", "ok"], available: ["Установлена", ""], loading: ["Загружается…", "warn"], missing: ["Не установлена", "warn"],
  error: ["Ошибка загрузки", "warn"], unsupported: ["Runtime недоступен", "warn"],
};
const pct = (v?: number) => (v === undefined ? "—" : `${Math.round(v * 1000) / 10} %`);
const num = (v: number | null | undefined, unit = "") => (v === null || v === undefined ? "—" : `${v}${unit}`);

function Metric({ r }: { r: AsrTestResult }) {
  return (
    <tr>
      <td><b>{r.title}</b><div className="muted small">{RUNTIME_LABEL[r.runtime] ?? r.runtime} · {r.quant ?? ""} · CPU</div></td>
      {r.ok ? (
        <>
          <td>{num(r.inference_ms, " мс")}<div className="muted small">на {r.audio_s} с аудио</div></td>
          <td>{num(r.rtf)}</td>
          <td>{num(r.cpu_s, " с")}<div className="muted small">≈ {num(r.cpu_cores_avg)} ядра</div></td>
          <td>{num(r.ram_mb, " МБ")}{r.ram_delta_mb != null && <div className="muted small">+{r.ram_delta_mb} МБ при загрузке</div>}</td>
          <td>{pct(r.wer)}<div className="muted small">CER {pct(r.cer)}</div></td>
          <td>{r.punctuation ? `${r.punctuation.hyp_marks} из ${r.punctuation.ref_marks}` : "—"}<div className="muted small">F1 {r.punctuation?.f1 ?? "—"}</div></td>
        </>
      ) : <td colSpan={6} className="small" style={{ color: "var(--danger-text)" }}>{r.error}</td>}
    </tr>
  );
}

/** Модели распознавания: статус, размер, runtime, устройство; тест и сравнение на встроенном аудио. Штатно доступна одна — полная GigaAM (PyTorch). */
export default function AsrModelsAdmin() {
  const [data, setData] = useState<AsrModels | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [tests, setTests] = useState<AsrTestResult[]>([]);
  const [cmp, setCmp] = useState<AsrCompare | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);

  const load = useCallback(() => api.admin.asrModels().then((d) => { setData(d); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  const pending = !!data && (!!data.loading_id || (!!data.desired && data.desired !== data.active_id && data.models.find((m) => m.id === data.desired)?.status !== "error"));
  useEffect(() => {
    const t = window.setInterval(load, pending ? 2000 : 15000);
    return () => window.clearInterval(t);
  }, [load, pending]);

  const choose = async (m: AsrModel) => {
    setBusy("choose"); setNote(null);
    try { const r = await api.admin.setAsrModel(m.id); setNote({ ok: true, text: `Выбрана «${m.title}». ${r.note}` }); await load(); }
    catch (e) { setNote({ ok: false, text: (e as ApiError).message }); }
    finally { setBusy(""); }
  };
  const test = async (m: AsrModel, force = false) => {
    setBusy(`test:${m.id}`); setNote(null); setBlocked(null);
    try {
      const r = await api.admin.asrTest(m.id, force);
      if (!r.ok && r.error && !r.title) setBlocked(r.error); else setTests((t) => [r, ...t.filter((x) => x.model_id !== r.model_id)]);
    } catch (e) { setNote({ ok: false, text: (e as ApiError).message }); }
    finally { setBusy(""); }
  };
  const compare = async (force = false) => {
    setBusy("compare"); setNote(null); setBlocked(null); setCmp(null);
    try { const r = await api.admin.asrCompare(force); if (!r.ok) setBlocked(r.error ?? "Сравнение не выполнено"); else setCmp(r); }
    catch (e) { setNote({ ok: false, text: (e as ApiError).message }); }
    finally { setBusy(""); }
  };

  if (err && !data) return <div className="alert error">{err}</div>;
  if (!data) return <div className="muted">Загрузка…</div>;
  const active = data.models.find((m) => m.id === data.active_id);
  const installed = data.models.filter((m) => m.present).length;

  return (
    <section>
      <h2>Распознавание речи (ASR)</h2>
      <p className="muted">Транскрибацию выполняет полная модель GigaAM v3 e2e RNNT (PyTorch): она распознаёт точнее квантованных вариантов, которые больше не поставляются. Здесь видно, загружена ли модель и на каком устройстве она работает, и можно проверить скорость на встроенном аудио. Если в каталоге появятся другие модели, выбор между ними выполняется без остановки: новая модель загружается в фоне, прежняя работает до готовности новой.</p>
      {!data.reachable && <div className="alert error" role="alert">{data.error}</div>}
      {note && <div className={`alert ${note.ok ? "ok" : "error"}`} role="status">{note.text}</div>}

      <div className="kpi">
        <div className="card"><div className="l">Активная модель</div><div className="v" style={{ fontSize: 16 }}>{active ? active.title : "нет"}</div>
          <div className="l">{active ? `${RUNTIME_LABEL[active.runtime] ?? active.runtime} · ${active.quant === "full" ? "полная точность" : active.quant}` : "модель не загружена"}</div></div>
        <div className="card"><div className="l">Устройство</div><div className="v">{(data.device ?? "cpu").toUpperCase()}</div>
          <div className="l">потоки: {data.threads?.intra ?? "—"} / inter-op {data.threads?.interop ?? "—"}</div></div>
        <div className="card"><div className="l">Состояние</div><div className="v" style={{ fontSize: 16 }}>{data.ready ? "готова" : data.loading_id ? "загружается…" : "не готова"}</div>
          <div className="l">{active?.load_ms ? `загрузка заняла ${(active.load_ms / 1000).toFixed(1)} с` : ""}</div></div>
        <div className="card"><div className="l">Установлено моделей</div><div className="v">{installed} из {data.models.length}</div></div>
        <div className="card" title="Средние по последним распознанным сегментам рабочей встречи"><div className="l">Рабочая задержка распознавания</div>
          <div className="v" style={{ fontSize: 16 }}>{data.live?.avg_infer_ms != null ? `${data.live.avg_infer_ms} мс` : "—"}</div>
          <div className="l">RTF {data.live?.rtf ?? "—"} · очередь {data.live?.avg_queue_ms ?? "—"} мс · отброшено {data.live?.dropped ?? "—"}</div></div>
      </div>

      {blocked && (
        <div className="alert" role="alert">{blocked}
          <button className="btn mini" onClick={() => (cmp === null && busy === "" ? compare(true) : undefined)}>Всё равно сравнить</button></div>
      )}

      <div className="asr-models">
        {data.models.map((m) => {
          const [label, cls] = STATUS[m.status] ?? [m.status, ""];
          const wanted = data.desired === m.id && !m.active;
          return (
            <div key={m.id} className={`card asr-model ${m.active ? "active" : ""}`}>
              <div className="row">
                <h3 style={{ margin: 0 }}>{m.title}</h3>
                <span className={`badge ${cls}`}>{wanted && m.status === "available" ? "Выбрана, загружается…" : label}</span>
                <div className="spacer" />
                <span className="badge">{RUNTIME_LABEL[m.runtime] ?? m.runtime}</span>
                <span className="badge">{m.quant === "full" ? "Full" : m.quant}</span>
                <span className="badge">CPU</span>
              </div>
              <p className="muted small" style={{ margin: "6px 0" }}>{m.description}</p>
              <div className="small">Размер: <b>{m.present ? bytes(m.size_bytes) : "—"}</b> · Файлы: <code>{m.files.join(", ")}</code></div>
              {!m.present && <div className="alert error small" role="alert">Модель не установлена: нет файлов <code>{m.missing.join(", ")}</code>. Положите их в каталог моделей на сервере (<code>DATA_ROOT/models/gigaam</code>) — например, <code>scripts/models.sh --gguf --from-dir …</code>. Текущая модель продолжает работать.</div>}
              {m.present && m.error && <div className="alert error small" role="alert">{m.error}</div>}
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn primary" disabled={m.active || !m.present || !!busy || m.status === "loading"} onClick={() => choose(m)}>{m.active ? "Активна" : "Сделать активной"}</button>
                <button className="btn" disabled={!m.present || !!busy} onClick={() => test(m)}>{busy === `test:${m.id}` ? "Тестирование…" : "Протестировать модель"}</button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="row" style={{ margin: "14px 0 6px" }}>
        <button className="btn primary" disabled={!!busy || installed < 2} onClick={() => compare(false)} title={installed < 2 ? "Нужны минимум две установленные модели" : ""}>
          {busy === "compare" ? "Сравнение… (до нескольких минут)" : "Сравнить установленные модели"}</button>
        <span className="muted small">Встроенный тестовый WAV ({data.test_audio_s ?? "?"} с, русская речь, синтез) с эталонным текстом: все модели прогоняются на одном и том же аудио. Не запускайте во время встреч.</span>
      </div>

      {(tests.length > 0 || cmp) && (
        <div className="card" style={{ marginTop: 10 }}>
          <h3>{cmp ? "Сравнение моделей" : "Результат теста"}</h3>
          <div style={{ overflowX: "auto" }}>
            <table className="table compact"><thead><tr><th>Модель</th><th>Скорость</th><th>RTF</th><th>CPU</th><th>RAM</th><th>Качество (WER)</th><th>Пунктуация</th></tr></thead>
              <tbody>{(cmp ? cmp.results : tests).map((r) => <Metric key={r.model_id} r={r} />)}</tbody></table>
          </div>
          {cmp && cmp.summary.length > 0 && <ul>{cmp.summary.map((s) => <li key={s}>{s}</li>)}</ul>}
          <details open>
            <summary>Тексты распознавания и эталон</summary>
            <p className="small"><b>Эталон:</b> {(cmp ?? tests[0])?.reference ?? (cmp as AsrCompare | null)?.reference ?? tests[0]?.reference}</p>
            {(cmp ? cmp.results : tests).filter((r) => r.ok).map((r) => <p key={r.model_id} className="small"><b>{r.title}:</b> {r.text}</p>)}
          </details>
          <p className="muted small">{cmp?.note ?? "RTF — время распознавания / длительность аудио (меньше — быстрее). CPU — процессорное время одного прогона. RAM — размер процесса ASR. WER/CER — доля ошибок слов/символов относительно эталона; пунктуация — сколько знаков поставлено и насколько они в тех же местах, что в эталоне. Синтезированная речь — только для сравнения моделей между собой, не абсолютная оценка качества."}</p>
        </div>
      )}
      <div style={{ marginTop: 16 }}>
        <SettingsForm key="asr-vad" group="asr" title="Параметры деления речи (VAD)" fields={vadFields}
          intro="Влияют на задержку и качество реплик: слишком короткая пауза дробит фразы, слишком длинная — задерживает текст. Применяются к новым трекам без перезапуска ASR; пустое поле — значение из .env. Проверьте на реальных разговорах: обрезание концов слов, короткие реплики, длинная речь, пунктуация." />
      </div>
      <p className="muted small">Все прогоны пишутся в журнал аудита. Метрики рабочего распознавания (inference_ms, audio_duration_ms, realtime_factor, queue_wait_ms) — в журнале ASR по каждому сегменту и в разделе «Состояние системы».</p>
    </section>
  );
}
