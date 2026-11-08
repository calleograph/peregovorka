import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent, type ReactNode } from "react";
import { api, type DiagEvent, type DiagHistoryItem, type DiagSample, type DiagState } from "../api";
import { Icon } from "../components/Icons";
import { copyText } from "../util";
import { Dot, Ring, Row, Spark, Tip, bps, bytes, kbps, level, lvClass, num, type Level } from "./gauges";
import { clientVerdict, type ClientStats, type ViewerStats } from "./viewerStats";
import "./livediag.css";

/** Панель диагностики эфира: сворачивается в индикатор, перетаскивается, лежит поверх страницы и не мешает ни кнопкам плеера, ни показу экрана (только читает готовые показатели). */
type Tab = "over" | "srv" | "flow" | "view" | "log" | "hist" | "me";
const POS_KEY = "pg:livediag:pos";
const OPEN_KEY = "pg:livediag:collapsed";
const KIND: Record<string, string> = { confirmed: "Подтверждено", assumed: "Предположение", unknown: "Нужна диагностика" };
const lsGet = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* только на этот показ */ } };
const hhmmss = (iso: string) => new Date(iso).toLocaleTimeString("ru-RU");
const since = (iso?: string | null, end?: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, Math.floor(((end ? new Date(end).getTime() : Date.now()) - new Date(iso).getTime()) / 1000)), two = (n: number) => String(n).padStart(2, "0");
  return `${two(Math.floor(s / 3600))}:${two(Math.floor(s / 60) % 60)}:${two(s % 60)}`;
};

function useDrag(el: React.RefObject<HTMLElement | null>) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(() => { try { return JSON.parse(lsGet(POS_KEY) ?? "null"); } catch { return null; } });
  const down = (e: RPointerEvent) => {
    if ((e.target as HTMLElement).closest("button,a") || window.innerWidth <= 560 || !el.current) return;
    const r = el.current.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top, w = r.width;
    const move = (m: PointerEvent) => setPos({ x: Math.max(0, Math.min(window.innerWidth - w, m.clientX - dx)), y: Math.max(0, Math.min(window.innerHeight - 40, m.clientY - dy)) });
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); setPos((p) => { if (p) lsSet(POS_KEY, JSON.stringify(p)); return p; }); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };
  // сохранённое положение могло остаться от большего экрана
  const style = pos ? { left: Math.min(pos.x, Math.max(0, window.innerWidth - 460)), top: Math.min(pos.y, Math.max(0, window.innerHeight - 200)) } : { right: 16, top: 64 };             // сверху справа: внизу кнопки плеера и панель управления встречей
  return { down, style };
}

function Overview({ d, range, setRange, series }: { d: DiagState; range: "ring" | "all"; setRange: (r: "ring" | "all") => void; series: DiagSample[] }) {
  const n = d.now, host = n?.host, eg = n?.svc?.egress, hls = n?.hls, v = n?.viewers;
  const col = (key: string) => { const i = d.keys.indexOf(key); return series.map((p) => p[i] as number | null); };
  const finds = n?.findings ?? [];
  return <>
    <div className="ld-chain" role="list" aria-label="Цепочка эфира">
      {(n?.stages ?? []).map((s) => <div key={s.id} role="listitem" className={`ld-stage ${lvClass(s.lv)}`}><span>{s.name}</span><b title={s.val ?? "нет данных"}>{s.val ?? "—"}</b></div>)}
    </div>
    {finds.length === 0 ? <div className="ld-fine"><Dot lv={0} /> Отклонений не найдено</div> : (
      <ul className="ld-finds" aria-live="polite">
        {finds.slice(0, 4).map((f) => <li key={f.code} className={`ld-find ${f.level === 2 ? "crit" : "warn"}`}><Dot lv={f.level as Level} /><b>{f.text}</b>{f.cause && <p><span className="ld-kind">{KIND[f.kind] ?? ""}:</span>{f.cause}</p>}</li>)}
      </ul>)}
    <div className="ld-rings">
      <Ring pct={host?.cpu} text={host?.cpu == null ? "—" : `${Math.round(host.cpu)}%`} label="Процессор" lv={level(host?.cpu, 85, 95)} sub={host?.cpus ? `${host.cpus} ядер` : undefined} tip="Загрузка всех ядер сервера. Выше 85 % кодировщику и серверу звонков может не хватить времени: растёт задержка, появляются пропуски кадров." />
      <Ring pct={host?.mem?.used_pct} text={host?.mem?.used_pct == null ? "—" : `${Math.round(host.mem.used_pct)}%`} label="Память" lv={level(host?.mem?.used_pct, 90, 95)} sub={bytes(host?.mem?.avail)} tip="Занято с учётом кэша, который система может освободить. Под кольцом — доступно. Когда памяти нет, Egress и браузер в нём завершаются аварийно." />
      <Ring pct={eg?.load} text={eg?.load == null ? "—" : `${Math.round(eg.load)}%`} label="Egress" lv={level(eg?.load, 85, 95)} sub={eg?.cores == null ? undefined : `${num(eg.cores)} ядра`} tip="Нагрузка узла Egress — та же величина, что проверяется перед запуском эфира. Под кольцом — сколько ядер занимает сам Egress (браузер и кодировщик). Egress собирает картинку и кодирует видео." />
      <Ring pct={host?.disk?.used_pct} text={host?.disk?.used_pct == null ? "—" : `${Math.round(host.disk.used_pct)}%`} label="Диск" lv={level(host?.disk?.used_pct, 85, 95)} sub={bytes(host?.disk?.free)} tip="Заполнение тома эфира, под кольцом — свободно. На нём лежат сегменты HLS и запись MP4: при нехватке места новые эфиры не начинаются." />
      <Ring pct={host?.net?.cap_bps && host.net.tx_bps != null ? host.net.tx_bps * 100 / host.net.cap_bps : null} text={host?.net?.tx_bps == null ? "—" : num(host.net.tx_bps / 1e6, 0)} label="Сеть" lv={host?.net?.cap_bps && host.net.tx_bps != null ? level(host.net.tx_bps * 100 / host.net.cap_bps, 90, 98) : -1} sub="Мбит/с" tip="Исходящий трафик сервера. Кольцо заполняется, если пропускная способность канала задана в «Администрирование → Трансляции»: сам сервер её не знает." />
    </div>
    <div className="ld-filter" role="group" aria-label="Период графиков">
      <button className="ld-chip" aria-pressed={range === "ring"} onClick={() => setRange("ring")}>30 минут</button>
      <button className="ld-chip" aria-pressed={range === "all"} onClick={() => setRange("all")}>Весь эфир</button>
    </div>
    <div className="ld-sparks">
      <Spark data={col("cpu")} max={100} label="Процессор" value={host?.cpu == null ? "—" : `${num(host.cpu, 0)} %`} lv={level(host?.cpu, 85, 95) === -1 ? 0 : level(host?.cpu, 85, 95)} />
      <Spark data={col("tx")} label="Исходящий трафик" value={bps(host?.net?.tx_bps)} />
      <Spark data={col("ready")} label="Сегмент готов" value={hls?.ready_s == null ? "—" : `${num(hls.ready_s)} с`} tip="Сколько проходит от конца сегмента до появления файла на диске: вклад Egress и записи в задержку." />
      <Spark data={col("lat")} label="Задержка у зрителя" value={v?.lat == null ? "—" : `${num(v.lat)} с`} tip="От момента в потоке до показа у зрителя, среднее по отчётам браузеров." />
      <Spark data={col("load")} max={100} label="Нагрузка Egress" value={eg?.load == null ? "—" : `${num(eg.load, 0)} %`} />
      <Spark data={col("viewers")} label="Зрителей" value={String(n?.viewer_count ?? "—")} />
    </div>
  </>;
}

const svcName: Record<string, string> = { backend: "Приложение", nginx: "nginx", livekit: "LiveKit", egress: "Egress", redis: "Redis", postgres: "PostgreSQL", asr: "Распознавание" };
const svcLv = (st?: string): Level => (st === "down" ? 2 : st === "unknown" || !st ? -1 : 0);

function Server({ d }: { d: DiagState }) {
  const n = d.now, h = n?.host, svc = n?.svc ?? {};
  return <>
    <section className="ld-sec"><h3>Процессор по ядрам<Tip>Высота столбца — загрузка ядра. Одно ядро под потолком при низкой общей загрузке: упор в однопоточную часть.</Tip></h3>
      <div className="ld-cores" role="img" aria-label="Загрузка ядер">{(h?.cores ?? []).map((c, i) => <i key={i} className={c >= 95 ? "crit" : c >= 85 ? "warn" : ""} style={{ height: `${Math.max(2, c)}%` }} title={`Ядро ${i}: ${c} %`} />)}</div>
      <div className="ld-grid2"><Row k="Всего" v={h?.cpu == null ? null : `${num(h.cpu)} %`} /><Row k="Нагрузка 1/5/15" v={h?.load ? h.load.map((x) => num(x, 1)).join(" / ") : null} /></div>
    </section>
    <section className="ld-sec"><h3>Память и диск</h3>
      <div className="ld-grid2">
        <Row k="Занято" v={h?.mem?.used_pct == null ? null : `${num(h.mem.used_pct)} %`} /><Row k="Доступно" v={bytes(h?.mem?.avail)} /><Row k="Свободно" v={bytes(h?.mem?.free)} /><Row k="Всего" v={bytes(h?.mem?.total)} />
        <Row k="Том: свободно" v={bytes(h?.disk?.free)} /><Row k="Том: всего" v={bytes(h?.disk?.total)} />
        <Row k="Чтение" v={h?.disk?.rd_bps == null ? null : `${bytes(h.disk.rd_bps)}/с`} na="Система хранения не даёт счётчиков этого устройства (например, сетевой том)." /><Row k="Запись" v={h?.disk?.wr_bps == null ? null : `${bytes(h.disk.wr_bps)}/с`} na="Система хранения не даёт счётчиков этого устройства." />
        <Row k="Занятость" v={h?.disk?.util == null ? null : `${num(h.disk.util, 0)} %`} tip="Доля времени, когда устройство выполняло операции." /><Row k="Задержка" v={h?.disk?.await_ms == null ? null : `${num(h.disk.await_ms)} мс`} tip="Среднее время одной операции ввода-вывода." />
      </div>
    </section>
    <section className="ld-sec"><h3>Сеть{h?.net?.if ? `: ${h.net.if}` : ""}<Tip>{h?.net?.scope === "host" ? "Счётчики интерфейсов сервера." : "Видна только сеть контейнера: файл счётчиков хоста не подключён, настоящий трафик сервера больше."}</Tip></h3>
      <div className="ld-grid2"><Row k="Принято" v={h?.net?.rx_bps == null ? null : bps(h.net.rx_bps)} /><Row k="Отправлено" v={h?.net?.tx_bps == null ? null : bps(h.net.tx_bps)} /><Row k="Пик" v={h?.net?.peak_bps == null ? null : bps(h.net.peak_bps)} /><Row k="Предел канала" v={h?.net?.cap_bps ? bps(h.net.cap_bps) : null} na="Задаётся в «Администрирование → Трансляции»." /><Row k="Ошибок/с" v={num(h?.net?.err_s, 1)} /><Row k="Отброшено/с" v={num(h?.net?.drop_s, 1)} /></div>
    </section>
    <section className="ld-sec"><h3>Службы<Tip>Состояние, время ответа, процессор и память службы. Прочерк — служба не отдаёт этот показатель. Перезапуски и зависания попадают в журнал.</Tip></h3>
      <div className="ld-scroll"><table className="ld-table"><thead><tr><th>Служба</th><th>Ответ</th><th>Ядер</th><th>Память</th><th>Ещё</th></tr></thead><tbody>
        {Object.keys(svcName).filter((k) => svc[k]).map((k) => { const s = svc[k]; return <tr key={k}><td><Dot lv={svcLv(s.st)} />{svcName[k]}</td><td>{s.ms == null ? "—" : `${num(s.ms, 0)} мс`}</td><td>{s.cores != null ? num(s.cores, 1) : s.cpu != null ? num(s.cpu / 100, 1) : "—"}</td><td>{bytes(s.mem)}</td>
          <td>{k === "nginx" && s.conns != null ? `${s.conns} соед.` : k === "redis" && s.ops != null ? `${s.ops} оп/с` : k === "backend" && s.lag_ms != null ? `цикл +${num(s.lag_ms, 0)} мс` : k === "asr" && s.queue != null ? `очередь ${s.queue}` : k === "egress" && s.queue != null ? `очередь ${s.queue}` : ""}</td></tr>; })}
      </tbody></table></div>
    </section>
  </>;
}

function Flow({ d }: { d: DiagState }) {
  const n = d.now, lk = n?.svc?.livekit, eg = n?.svc?.egress, m = n?.media, hls = n?.hls, src = n?.source, pubs = n?.viewers?.publishers ?? [], stages = d.stages ?? [];
  return <>
    <section className="ld-sec"><h3>1. Источник<Tip>Что публикуют участники (то, что присылает выступающий; качество самого эфира выбрано в настройках и показано справа от стрелки на плитке этапа и в блоке «Поток на выходе»). Разрешение, кадры и причина снижения качества приходят из браузера выступающего, если в нём открыта комната.</Tip></h3>
      {(src?.list ?? []).length === 0 && <p className="ld-empty">Участников с дорожками нет</p>}
      {(src?.list ?? []).filter((p) => p.tracks.length).map((p) => <Row key={p.id} k={p.name} v={p.tracks.map((t) => (t.kind === "video" ? `${t.src === "screen" ? "экран" : "камера"} ${t.w ?? "?"}×${t.h ?? "?"}${t.muted ? " (выкл.)" : ""}` : t.muted ? "звук выкл." : "звук")).join(" · ")} />)}
      {pubs.map((p, i) => <Row key={i} k={`Браузер: ${p.src === "screen" ? "экран" : "камера"}`} v={`${p.res?.replace("x", "×") ?? "?"} · ${num(p.fps, 0)} к/с · ${kbps(p.kbps)} · потери ${num(p.loss, 1)} %${p.limit && p.limit !== "none" ? ` · ограничение: ${p.limit === "cpu" ? "процессор" : p.limit === "bandwidth" ? "канал" : "другое"}` : ""}`} />)}
    </section>
    <section className="ld-sec"><h3>2. LiveKit<Tip>Показатели всего сервера звонков, а не только этого эфира.</Tip></h3>
      <div className="ld-grid2"><Row k="Принято" v={kbps(lk?.in_kbps)} /><Row k="Отправлено" v={kbps(lk?.out_kbps)} /><Row k="Потери от участников" v={lk?.loss_in == null ? null : `${num(lk.loss_in)} %`} /><Row k="Потери к участникам" v={lk?.loss_out == null ? null : `${num(lk.loss_out)} %`} /><Row k="Повторы NACK/с" v={num(lk?.nack_s, 1)} tip="Запросы повторной отправки пакетов: растут при потерях в сети." /><Row k="Запросы кадра/с" v={num(lk?.pli_s, 2)} tip="Получатель потерял картинку и просит ключевой кадр." /></div>
    </section>
    <section className="ld-sec"><h3>3. Egress<Tip>Браузер внутри Egress собирает картинку комнаты, кодировщик выдаёт один поток для HLS, RTMP и записи.</Tip></h3>
      <div className="ld-grid2"><Row k="Состояние" v={n?.egress_status?.replace("EGRESS_", "") ?? null} /><Row k="Заданий" v={num(eg?.jobs, 0)} /><Row k="Нагрузка узла" v={eg?.load == null ? null : `${num(eg.load, 0)} %`} /><Row k="Ядер занято" v={num(eg?.cores, 2)} na="Обновляется раз в 15 с; при cgroup v1 недоступно." /><Row k="Память" v={bytes(eg?.mem)} /><Row k="Очередь записи" v={num(eg?.queue, 0)} tip="Сегменты, ожидающие записи: растёт, когда диск не успевает." /><Row k="Запись файла" v={eg?.upload_ms == null ? null : `${num(eg.upload_ms)} мс`} /><Row k="Принимает задания" v={eg?.accept == null ? null : eg.accept ? "да" : "нет"} /></div>
    </section>
    <section className="ld-sec"><h3>4. Поток на выходе<Tip>Разбор последнего сегмента раз в 30 с: реальные разрешение, кадры и битрейт, а не заказанные значения. Сдвиг звука — разница начала дорожек внутри сегмента: насколько расходятся звук и изображение в самом начале сегмента, рассинхронизацию у источника он не показывает.</Tip></h3>
      <div className="ld-grid2"><Row k="Заказано" v={n?.quality} /><Row k="Разрешение" v={m?.res?.replace("x", "×")} /><Row k="Кадров/с" v={num(m?.fps, 1)} /><Row k="Пропуски кадров" v={num(m?.gaps, 0)} /><Row k="Видео" v={kbps(m?.vkbps)} /><Row k="Звук" v={kbps(m?.akbps)} /><Row k="Сдвиг звука" v={m?.av_ms == null ? null : `${m.av_ms} мс`} /><Row k="Кодеки" v={m?.vcodec ? `${m.vcodec} + ${m.acodec ?? "—"}` : null} /></div>
    </section>
    {stages.length > 0 && <section className="ld-sec"><h3>Запуск<Tip>Этапы запуска этого эфира и время от нажатия кнопки. Повтор означает, что браузер службы не поднялся за 20 с и запуск прошёл заново.</Tip></h3>
      {stages.map((e) => <Row key={e.id} k={e.message.split(" за ")[0]} v={e.message.includes(" за ") ? e.message.split(" за ").pop() : "—"} />)}
    </section>}
    {hls && <section className="ld-sec"><h3>HLS</h3>
      <div className="ld-grid2">{hls.ll !== undefined && <Row k="Низкая задержка" v={hls.ll ? "работает" : "нет, зрители на обычном HLS"} tip="Поток LL-HLS. Отказ этой службы не трогает запись и площадки." />}<Row k="Длина сегмента" v={hls.dur_s == null ? null : `${num(hls.dur_s, 2)} с`} /><Row k="Интервал" v={hls.interval_s == null ? null : `${num(hls.interval_s, 2)} с`} tip="Как часто появляются новые сегменты. Больше длины сегмента — поток запаздывает." /><Row k="Сегмент готов" v={hls.ready_s == null ? null : `${num(hls.ready_s, 2)} с`} /><Row k="Последний" v={hls.age_s == null ? null : `${num(hls.age_s)} с назад`} /><Row k="Размер" v={bytes(hls.bytes)} /><Row k="Поток" v={kbps(hls.kbps)} /></div>
    </section>}
    {(n?.rtmp ?? []).length > 0 && <section className="ld-sec"><h3>RTMP<Tip>Состояние по ответу Egress. Скорость отдельно по площадкам Egress не сообщает: у всех один поток кодировщика (битрейт — в разделе выше). Ключи потоков не показываются и не записываются.</Tip></h3>
      <div className="ld-scroll"><table className="ld-table"><thead><tr><th>Площадка</th><th>Состояние</th><th>Повторы</th><th>Смена состояний</th></tr></thead><tbody>
        {n!.rtmp!.map((r) => <tr key={r.id} title={r.error ?? undefined}><td><Dot lv={r.status === "failed" ? 2 : r.status === "live" ? 0 : 1} />{r.name}</td><td>{r.status}</td><td>{r.retries ?? "—"}</td><td>{r.flips ?? 0}</td></tr>)}
      </tbody></table></div>
    </section>}
  </>;
}

function Viewers({ d }: { d: DiagState }) {
  const n = d.now, v = n?.viewers;
  return <>
    <section className="ld-sec"><h3>Зрители<Tip>Браузеры зрителей присылают показатели вместе с пульсом раз в 30 с и сразу при проблеме. Если проблема у одного зрителя — это его канал, если у многих — общая доставка.</Tip></h3>
      <div className="ld-grid2"><Row k="Смотрят сейчас" v={n?.viewer_count ?? 0} /><Row k="Прислали отчёт" v={v?.reporting ?? 0} /><Row k="С проблемой" v={v?.bad ?? 0} /><Row k="Остановок за 6 с" v={v?.stalls ?? 0} /><Row k="Задержка" v={v?.lat == null ? null : `${num(v.lat)} с`} /><Row k="Задержка, 95 %" v={v?.lat_p95 == null ? null : `${num(v.lat_p95)} с`} /><Row k="Загрузка сегмента" v={v?.seg_ms == null ? null : `${num(v.seg_ms, 0)} мс`} /><Row k="Сегмент, 95 %" v={v?.seg_ms_p95 == null ? null : `${num(v.seg_ms_p95, 0)} мс`} /><Row k="Буфер" v={v?.buf == null ? null : `${num(v.buf)} с`} /><Row k="Скорость канала" v={kbps(v?.est)} /></div>
    </section>
    {(v?.worst ?? []).length > 0 && <section className="ld-sec"><h3>Хуже всего</h3>
      <div className="ld-scroll"><table className="ld-table"><thead><tr><th>Зритель</th><th>Сегмент</th><th>Буфер</th><th>Причина</th></tr></thead><tbody>
        {v!.worst.map((w) => <tr key={w.id}><td><Dot lv={w.lv as Level} />{w.ua ?? w.id}</td><td>{w.seg_ms == null ? "—" : `${num(w.seg_ms, 0)} мс`}</td><td>{w.buf == null ? "—" : `${num(w.buf)} с`}</td><td>{w.why}</td></tr>)}
      </tbody></table></div></section>}
    {(n?.cdn ?? []).length > 0 && <section className="ld-sec"><h3>Узлы CDN</h3>
      <div className="ld-scroll"><table className="ld-table"><thead><tr><th>Узел</th><th>Зрителей</th><th>Трафик</th><th>Кеш</th></tr></thead><tbody>
        {n!.cdn!.map((c) => <tr key={c.name}><td><Dot lv={c.state === "ready" ? 0 : c.state === "error" ? 2 : 1} />{c.name}</td><td>{c.viewers ?? "—"}</td><td>{c.mbit == null ? "—" : `${num(c.mbit, 1)} Мбит/с`}</td><td>{c.hit == null ? "—" : `${num(c.hit * 100, 0)} %`}</td></tr>)}
      </tbody></table></div></section>}
  </>;
}

const COMPONENTS: [string, string][] = [["", "Все компоненты"], ["broadcast", "Эфир"], ["server", "Сервер"], ["source", "Источник"], ["livekit", "LiveKit"], ["egress", "Egress"], ["hls", "HLS"], ["rtmp", "RTMP"], ["delivery", "Доставка"], ["viewer", "Зрители"]];

function Journal({ bid, state }: { bid: string; state: DiagState }) {
  const [lv, setLv] = useState("");
  const [comp, setComp] = useState("");
  const [items, setItems] = useState<DiagEvent[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const load = useCallback(() => { api.liveDiagEvents(bid, { level: lv || undefined, component: comp || undefined }).then((r) => setItems(r.items)).catch(() => setNote("Журнал не загрузился")); }, [bid, lv, comp]);
  useEffect(() => { load(); if (state.status === "ended" || state.status === "failed") return; const t = window.setInterval(load, 6000); return () => window.clearInterval(t); }, [load, state.status]);
  const t0 = new Date(state.started_at ?? 0).getTime(), t1 = state.ended_at ? new Date(state.ended_at).getTime() : Date.now(), span = Math.max(1, t1 - t0);
  const copy = async () => { const text = await fetch(api.liveDiagExportUrl(bid, "txt"), { credentials: "same-origin" }).then((r) => r.text()).catch(() => ""); const ok = !!text && await copyText(text); setNote(ok ? "Журнал скопирован" : "Не удалось скопировать"); window.setTimeout(() => setNote(""), 2500); };
  return <>
    <div className="ld-filter" role="group" aria-label="Фильтр журнала">
      {[["", "Все"], ["warn,error", "Проблемы"], ["error", "Ошибки"]].map(([k, l]) => <button key={k} className="ld-chip" aria-pressed={lv === k} onClick={() => setLv(k)}>{l}</button>)}
      <select aria-label="Компонент" value={comp} onChange={(e) => setComp(e.target.value)}>{COMPONENTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
    </div>
    <svg className="ld-line" viewBox="0 0 400 20" preserveAspectRatio="none" role="img" aria-label="Шкала событий эфира">
      {items.map((e) => <rect key={e.id} className={e.level} x={Math.min(398, Math.max(0, (new Date(e.at).getTime() - t0) / span * 400))} y={e.level === "info" ? 10 : 2} width="2" height={e.level === "info" ? 8 : 16}><title>{`${hhmmss(e.at)} ${e.message}`}</title></rect>)}
    </svg>
    <div className="ld-acts">
      <button className="ld-act" onClick={() => void copy()}><Icon name="copy" size={13} /> Копировать</button>
      <a className="ld-act" href={api.liveDiagExportUrl(bid, "txt")} download><Icon name="download" size={13} /> TXT</a>
      <a className="ld-act" href={api.liveDiagExportUrl(bid, "json")} download><Icon name="download" size={13} /> JSON</a>
      <a className="ld-act" href={api.liveDiagExportUrl(bid, "report")} download><Icon name="download" size={13} /> Отчёт</a>
      {note && <span role="status">{note}</span>}
    </div>
    {items.length === 0 ? <p className="ld-empty">Событий нет</p> : (
      <ol className="ld-ev">
        {items.map((e) => <li key={e.id}><time>{hhmmss(e.at)}</time><Dot lv={e.level === "error" ? 2 : e.level === "warn" ? 1 : 0} />
          <button aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)}>{e.message}</button>
          {e.cause && <p><span className="ld-kind">{KIND[e.kind ?? ""] ?? "Причина"}:</span>{e.cause}</p>}
          {open === e.id && e.details && <pre>{JSON.stringify(e.details, null, 1)}</pre>}</li>)}
      </ol>)}
  </>;
}

const CMP: [string, (h: DiagHistoryItem) => string][] = [
  ["Качество", (h) => h.quality], ["Разрешение", (h) => h.summary?.res?.replace("x", "×") ?? "—"], ["Режим", (h) => { const m = h.summary?.mode; return m ? [m.hls && "HLS", m.rtmp ? `RTMP ${m.rtmp}` : "", m.cdn && "CDN"].filter(Boolean).join(" + ") || "—" : "—"; }],
  ["Длительность", (h) => since(h.started_at, h.ended_at)], ["Зрителей, пик", (h) => String(h.peak_viewers)], ["Процессор, ср.", (h) => (h.summary?.cpu ? `${num(h.summary.cpu.avg, 0)} %` : "—")], ["Процессор, макс.", (h) => (h.summary?.cpu ? `${num(h.summary.cpu.max, 0)} %` : "—")],
  ["Egress, ядер", (h) => (h.summary?.egress_cores ? num(h.summary.egress_cores.avg, 1) : "—")], ["Трафик, макс.", (h) => (h.summary?.tx_mbit ? `${num(h.summary.tx_mbit.max, 0)} Мбит/с` : "—")], ["Видео", (h) => kbps(h.summary?.video_kbps?.avg)],
  ["Сегмент готов", (h) => (h.summary?.seg_ready_s ? `${num(h.summary.seg_ready_s.avg)} с` : "—")], ["Задержка зрителя", (h) => (h.summary?.latency_s ? `${num(h.summary.latency_s.avg)} с` : "—")], ["Остановок", (h) => String(h.summary?.stalls ?? "—")],
  ["Ошибок/предупр.", (h) => `${h.summary?.events?.error ?? 0} / ${h.summary?.events?.warn ?? 0}`], ["Версия", (h) => h.summary?.version ?? "—"],
];

/** История эфиров комнаты (или всех, в администрировании) и сравнение выбранных: нагрузка, качество и задержка рядом. */
export function DiagHistory({ load }: { load: () => Promise<{ items: DiagHistoryItem[] }> }) {
  const [items, setItems] = useState<DiagHistoryItem[] | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => { load().then((r) => setItems(r.items)).catch(() => setErr("История не загрузилась")); }, [load]);
  const picked = useMemo(() => (items ?? []).filter((h) => sel.includes(h.id)), [items, sel]);
  if (err) return <p className="ld-empty">{err}</p>;
  if (!items) return <p className="ld-empty">Загрузка…</p>;
  if (!items.length) return <p className="ld-empty">Завершённых эфиров с диагностикой пока нет</p>;
  return <>
    {picked.length >= 2 && <section className="ld-sec"><h3>Сравнение</h3>
      <div className="ld-scroll"><div className="ld-pair" style={{ "--n": picked.length } as React.CSSProperties}>
        <span />{picked.map((h) => <span key={h.id} className="h">{new Date(h.started_at).toLocaleDateString("ru-RU")} {hhmmss(h.started_at).slice(0, 5)}</span>)}
        {CMP.flatMap(([k, f]) => [<span key={k}>{k}</span>, ...picked.map((h) => <b key={k + h.id}>{f(h)}</b>)])}
      </div></div></section>}
    <section className="ld-sec"><h3>Эфиры<Tip>Отметьте два эфира и больше, чтобы сравнить. Итог эфира хранится и после удаления подробных показателей по сроку.</Tip></h3>
      <div className="ld-scroll"><table className="ld-table"><thead><tr><th>Эфир</th><th>Зрит.</th><th>Проц.</th><th>Ошибок</th><th>Отчёт</th></tr></thead><tbody>
        {items.map((h) => <tr key={h.id}><td><label className="check"><input type="checkbox" checked={sel.includes(h.id)} onChange={(e) => setSel((x) => (e.target.checked ? [...x, h.id].slice(-4) : x.filter((i) => i !== h.id)))} /> {new Date(h.started_at).toLocaleDateString("ru-RU")} {hhmmss(h.started_at).slice(0, 5)} · {h.title || h.room}</label></td>
          <td>{h.peak_viewers}</td><td>{h.summary?.cpu ? `${num(h.summary.cpu.avg, 0)} %` : "—"}</td><td>{h.summary?.events?.error ?? "—"}</td><td><a className="ld-act" href={api.liveDiagExportUrl(h.id, "report")} download aria-label="Скачать отчёт"><Icon name="download" size={13} /></a></td></tr>)}
      </tbody></table></div></section>
  </>;
}

/** Мой просмотр: только то, что видит браузер этого зрителя. */
function Mine({ viewer, c, simple }: { viewer: ViewerStats; c: ClientStats; simple: boolean }) {
  const baseline = useRef({ stalls: c.stalls, at: Date.now() });
  if (Date.now() - baseline.current.at > 15_000) baseline.current = { stalls: c.stalls, at: Date.now() };
  const v = clientVerdict(c, baseline.current.stalls), native = c.eng === "native";
  const noHls = native ? "Этот браузер воспроизводит поток сам и не сообщает странице время загрузки сегментов." : "Пока ни один сегмент не загружен.";
  const h = viewer.history;
  const tech = <>
    <div className="ld-rings">
      <Ring pct={c.buf == null ? null : c.buf * 10} text={c.buf == null ? "—" : num(c.buf)} label="Буфер" lv={c.buf == null ? -1 : c.buf < 0.3 ? 2 : c.buf < 1 ? 1 : 0} sub="с" tip="Сколько видео уже загружено наперёд. Опустеет — картинка остановится." />
      <Ring pct={c.lag == null ? null : c.lag * 100 / 15} text={c.lag == null ? "—" : num(c.lag)} label="Отставание" lv={level(c.lag, 12, 30)} sub="с" tip="На сколько вы позади края эфира. Плеер держит около 3 с и догоняет сам." />
      <Ring pct={c.seg_ms == null ? null : c.seg_ms / 15} text={c.seg_ms == null ? "—" : num(c.seg_ms, 0)} label="Сегмент" lv={level(c.seg_ms, 1200, 1500)} sub="мс" tip="Время загрузки одного сегмента. Сегмент длится около 1,5 с: если загрузка дольше, плеер не успевает." />
      <Ring pct={c.fps == null ? null : c.fps * 100 / 30} text={c.fps == null ? "—" : num(c.fps, 0)} label="Кадры" lv={c.fps == null ? -1 : c.fps < 15 ? 1 : 0} sub="к/с" tip="Сколько кадров в секунду показывает браузер." />
      <Ring pct={null} text={c.lat == null ? "—" : num(c.lat)} label="Задержка" lv={level(c.lat, 15, 30)} sub="с" tip="От момента в потоке до показа у вас, по часам сервера." />
    </div>
    <div className="ld-sparks">
      <Spark data={h.map((p) => p.buf)} label="Буфер" value={c.buf == null ? "—" : `${num(c.buf)} с`} />
      <Spark data={h.map((p) => p.seg_ms)} label="Загрузка сегмента" value={c.seg_ms == null ? "—" : `${num(c.seg_ms, 0)} мс`} />
    </div>
    <div className="ld-grid2">
      <Row k="Разрешение" v={c.res?.replace("x", "×")} /><Row k="Кадров потеряно" v={c.drop} na="Браузер не сообщает." />
      <Row k="Поток" v={c.kbps == null ? null : kbps(c.kbps)} na={noHls} /><Row k="Скорость канала" v={c.est == null ? null : kbps(c.est)} na={noHls} tip="Оценка плеера по скорости загрузки сегментов." />
      <Row k="Размер сегмента" v={c.seg_kb == null ? null : `${num(c.seg_kb, 0)} КБ`} na={noHls} /><Row k="Остановок" v={c.stalls} />
      <Row k="Остановлено всего" v={`${num(c.stall_ms / 1000)} с`} /><Row k="Переподключений" v={c.reconn} /><Row k="Ошибок" v={c.errs} /><Row k="Плеер" v={native ? "встроенный" : "hls.js"} />
      {c.node && <Row k="Узел CDN" v={c.node} />}
    </div>
  </>;
  // Зрителю — вердикт и четыре понятных показателя; кольца, графики и служебные строки (плеер, узел, размер сегмента) — под «Подробнее». Организатор видит всё сразу.
  return <>
    <div className={v.level ? `ld-find ${v.level === 2 ? "crit" : "warn"}` : "ld-fine"} role="status"><Dot lv={v.level} /> <b>{v.text}</b></div>
    {simple ? <>
      <div className="ld-grid2">
        <Row k="Показ" v={c.aud ? "только звук" : "видео"} /><Row k="Качество" v={c.res?.replace("x", "×")} na="Пока неизвестно." />
        <Row k="Задержка" v={c.lat == null ? null : `${num(c.lat)} с`} na="Пока неизвестно." /><Row k="Остановок" v={c.stalls} />
      </div>
      <details className="ld-more"><summary>Подробнее</summary>{tech}</details>
    </> : tech}
  </>;
}

export default function LiveDiagPanel({ meetingId, roomId, viewer, onClose }: { meetingId?: string | null; roomId?: string | null; viewer?: ViewerStats; onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
  const [mini, setMini] = useState(() => lsGet(OPEN_KEY) === "1");
  const server = !!meetingId;
  const [tab, setTab] = useState<Tab>(server ? "over" : "me");
  const [d, setD] = useState<DiagState | null>(null);
  const [ring, setRing] = useState<DiagSample[]>([]);
  const [all, setAll] = useState<DiagSample[]>([]);
  const [range, setRange] = useState<"ring" | "all">("ring");
  const [err, setErr] = useState("");
  const [c, setC] = useState<ClientStats | null>(viewer ? viewer.tick() : null);
  const { down, style } = useDrag(box);
  const n = useRef(0);

  useEffect(() => { lsSet(OPEN_KEY, mini ? "1" : "0"); }, [mini]);
  useEffect(() => () => opener.current?.focus?.({ preventScroll: true }), []);
  useEffect(() => { if (!mini) box.current?.focus({ preventScroll: true }); }, [mini]);        // только по действию пользователя: опрос сервера фокус не трогает

  useEffect(() => {
    if (!meetingId) return;
    let alive = true;
    const pull = () => {
      if (document.visibilityState !== "visible") return;
      const full = n.current++ % 6 === 0;
      api.liveDiag(meetingId, { ring: full, events: false }).then((r) => { if (!alive) return; setD(r); setErr(""); if (full) setRing(r.ring); }).catch((e) => { if (alive) setErr((e as Error).message || "Нет связи с сервером"); });
    };
    pull();
    const t = window.setInterval(pull, mini ? 10_000 : 2_000);
    return () => { alive = false; window.clearInterval(t); };
  }, [meetingId, mini]);
  useEffect(() => { if (range !== "all" || !d?.bid) return; const bid = d.bid; const f = () => api.liveDiagSeries(bid).then((r) => setAll(r.points)).catch(() => undefined); void f(); const t = window.setInterval(f, 30_000); return () => window.clearInterval(t); }, [range, d?.bid]);
  useEffect(() => { if (!viewer) return; const t = window.setInterval(() => setC(viewer.tick()), 1000); return () => window.clearInterval(t); }, [viewer]);

  const stages = d?.now?.stages ?? [];
  const worst = Math.max(-1, ...stages.map((s) => s.lv), ...(d?.now?.findings ?? []).map((f) => f.level)) as Level;
  const clv = c ? clientVerdict(c, c.stalls).level : -1;
  const lv = (server ? worst : clv) as Level;
  const close = () => onClose();
  const keys = (e: React.KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setMini(true); } };

  if (mini) return (
    <button className="ld-pill" style={style} aria-label="Развернуть диагностику эфира" onClick={() => setMini(false)}>
      <Dot lv={lv} />{server ? <>{d?.now?.host?.cpu == null ? "—" : `${Math.round(d.now.host.cpu)} %`} · {d?.now?.viewer_count ?? 0} зр.</> : <>{c?.buf == null ? "—" : `${num(c.buf)} с`}</>}
    </button>);
  const tabs: [Tab, string][] = server ? [["over", "Обзор"], ["srv", "Сервер"], ["flow", "Поток"], ["view", "Зрители"], ["log", "Журнал"], ...(roomId ? [["hist", "История"] as [Tab, string]] : []), ...(viewer ? [["me", "Мой просмотр"] as [Tab, string]] : [])] : [];
  const live = d?.status === "live" || d?.status === "starting" || d?.status === "ending";
  let content: ReactNode = null;
  if (tab === "me" && viewer && c) content = <Mine viewer={viewer} c={c} simple={!server} />;
  else if (tab === "hist" && roomId) content = <DiagHistory load={() => api.liveDiagHistory(roomId)} />;
  else if (server && err && !d) content = <p className="ld-empty" role="alert">{err}</p>;
  else if (server && !d) content = <p className="ld-empty">Загрузка…</p>;
  else if (d && !d.enabled) content = <p className="ld-empty">Диагностика эфиров выключена администратором.</p>;
  else if (d && !d.bid) content = <p className="ld-empty">Эфиров в этой комнате ещё не было.</p>;
  else if (d && tab === "log") content = <Journal bid={d.bid!} state={d} />;
  else if (d && !d.now) content = <p className="ld-empty">{live ? "Показатели появятся через несколько секунд." : "Эфир завершён: откройте «Журнал» или «История»."}</p>;
  else if (d && tab === "over") content = <Overview d={d} range={range} setRange={setRange} series={range === "all" ? all : ring} />;
  else if (d && tab === "srv") content = <Server d={d} />;
  else if (d && tab === "flow") content = <Flow d={d} />;
  else if (d && tab === "view") content = <Viewers d={d} />;
  return (
    <div ref={box} className="ld" style={style} role="dialog" aria-label="Диагностика эфира" tabIndex={-1} data-no-autofocus onKeyDown={keys}>
      <div className="ld-head" onPointerDown={down}>
        <Dot lv={lv} /><b>{server ? "Диагностика" : "Мой просмотр"}</b>
        {d?.started_at && <span className="ld-clock">{since(d.started_at, d.ended_at)}</span>}
        {err && d && <span className="ld-clock" role="status">нет связи</span>}
        <span className="sp" />
        <button className="ld-x" aria-label="Свернуть" onClick={() => setMini(true)}><Icon name="mini" size={16} /></button>
        <button className="ld-x" aria-label="Закрыть" onClick={close}><Icon name="close" size={16} /></button>
      </div>
      {tabs.length > 0 && <div className="ld-tabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>)}</div>}
      <div className="ld-body">{content}</div>
    </div>
  );
}
