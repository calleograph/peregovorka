// Диагностика подключения к серверу звонков: что и на каком этапе не получилось, без секретов в журналах.
//
// Этапы: 1) сигнальное соединение (WebSocket до LiveKit) → 2) ICE (выбор пути для звука и видео) → 3) медиа.
// Токен доступа и join_request живут только в запросе браузера к серверу; в наши журналы, события и текст ошибок они не попадают:
// всё, что уходит на сервер или показывается, проходит redactSecrets().

/** Удаляет токены и длинные служебные значения из текста (URL с access_token/join_request, JWT, Bearer) и укорачивает результат. */
export function redactSecrets(text: unknown, max = 400): string {
  let t = String(text ?? "");
  t = t.replace(/(access_token|token|join_request|authorization|auth)=([^&\s"'<>]*)/gi, (_m, k: string, v: string) => `${k}=<скрыто, ${v.length} симв.>`);
  t = t.replace(/eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}/g, "<jwt скрыт>");
  t = t.replace(/Bearer\s+\S+/gi, "Bearer <скрыто>");
  return t.length > max ? `${t.slice(0, max)}… (+${t.length - max} симв.)` : t;
}

/** Адрес без параметров, логина и фрагмента: https://host/livekit — этого достаточно для разбора. */
export function safeUrl(raw: string): string {
  try {
    const u = new URL(raw);
    return `${u.protocol}//${u.host}${u.pathname.replace(/\/$/, "")}`;
  } catch { return redactSecrets(raw, 120); }
}

/** HTTP-адрес корня сигнального сервера по wss://-адресу (LiveKit отвечает «OK» на GET /). */
export function signalHttpUrl(wsUrl: string): string {
  const u = safeUrl(wsUrl).replace(/^wss:/, "https:").replace(/^ws:/, "http:");
  return u.endsWith("/") ? u : `${u}/`;
}

export interface SignalProbe { ok: boolean; status?: number; ms: number; error?: string }

/** Проверка доступности сигнального сервера обычным HTTPS-запросом (тот же путь и прокси, что у WebSocket). Не передаёт токенов. */
export async function probeSignal(wsUrl: string, timeoutMs = 6000, fetchFn: typeof fetch = fetch): Promise<SignalProbe> {
  const t0 = performance.now();
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetchFn(signalHttpUrl(wsUrl), { cache: "no-store", signal: ctl.signal });
    return { ok: r.ok, status: r.status, ms: Math.round(performance.now() - t0) };
  } catch (e) {
    const aborted = (e as { name?: string })?.name === "AbortError";
    return { ok: false, ms: Math.round(performance.now() - t0), error: aborted ? "timeout" : "network_error" };
  } finally { clearTimeout(timer); }
}

export function describeProbe(p: SignalProbe): string {
  if (p.ok) return `HTTPS до сервера звонков отвечает (${p.status}, ${p.ms} мс)`;
  if (p.status) return `HTTPS до сервера звонков вернул ${p.status} (${p.ms} мс)`;
  if (p.error === "timeout") return `HTTPS до сервера звонков не ответил за ${Math.round(p.ms / 1000)} с`;
  return `HTTPS до сервера звонков не удался за ${p.ms} мс (соединение отклонено или сброшено)`;
}

/** Куда в цепочке «сигнал → ICE → медиа» попала ошибка. */
export type FailStage = "signal" | "ice" | "unknown";

/** Этап по тексту ошибки; stage — этап, на котором была запущена попытка (когда по тексту не ясно). */
export function failStage(e: unknown, stage?: "server" | "media"): FailStage {
  const t = String((e as { message?: unknown })?.message ?? e ?? "").toLowerCase();
  if (/pc connection|ice (connection|failed)|peerconnection|ice_failed/.test(t)) return "ice";
  if (/connection_reset|connection reset|failed to fetch|websocket|signal|serverunreachable|network|timeout|timed out/.test(t)) return stage === "media" ? "ice" : "signal";
  return stage === "media" ? "ice" : stage === "server" ? "signal" : "unknown";
}

/** Данные о соединении для события в журнале: безопасный адрес и результат проверки, время сигнала, ICE, выбранная пара кандидатов. */
export interface ConnectionReport {
  livekit_url: string; signal_probe?: SignalProbe; signal_ms?: number | null; ice_ms?: number | null; fail_stage?: FailStage;
  ice_state?: string; connection_state?: string; transport?: string; local?: string; remote?: string; rtt_ms?: number | null;
}

/** Сбор сведений о пути через RTCPeerConnection комнаты LiveKit. Любой сбой — просто нет данных (диагностика не должна ломать звонок). */
export async function describeConnection(room: unknown): Promise<Partial<ConnectionReport>> {
  try {
    const eng = (room as { engine?: { pcManager?: { publisher?: { pc?: RTCPeerConnection }; subscriber?: { pc?: RTCPeerConnection } } } }).engine;
    const pc = eng?.pcManager?.publisher?.pc ?? eng?.pcManager?.subscriber?.pc;
    if (!pc) return {};
    const out: Partial<ConnectionReport> = { ice_state: pc.iceConnectionState, connection_state: pc.connectionState };
    const rep = await pc.getStats();
    const all: Record<string, unknown>[] = [];
    rep.forEach((v: Record<string, unknown>) => all.push(v));
    const byId = new Map(all.map((r) => [r.id as string, r]));
    const tr = all.find((r) => r.type === "transport" && r.selectedCandidatePairId);
    const pair = (tr && byId.get(tr.selectedCandidatePairId as string)) ?? all.find((r) => r.type === "candidate-pair" && (r.nominated || r.selected) && r.state !== "failed");
    if (pair) {
      const l = byId.get(pair.localCandidateId as string), r = byId.get(pair.remoteCandidateId as string);
      const fmt = (c?: Record<string, unknown>) => (c ? `${c.candidateType ?? "?"} ${c.address ?? c.ip ?? "?"}:${c.port ?? "?"}` : undefined);
      out.transport = String(l?.protocol ?? r?.protocol ?? "");
      out.local = fmt(l); out.remote = fmt(r);
      const rtt = pair.currentRoundTripTime;
      out.rtt_ms = typeof rtt === "number" ? Math.round(rtt * 1000) : null;
    }
    return out;
  } catch { return {}; }
}
