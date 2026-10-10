import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type StageState } from "../api";
import {
  SpeakerTracker, buildItems, chooseMain, keyOf, parseKey, parsePersonal, storageKey, stripOrder, togglePin, trackSince,
  type Choice, type LayoutMode, type Personal, type Source, type StageItem,
} from "./stageModel";

export interface StageSource extends Source { speaking: boolean }

export interface MeetingStage {
  items: StageItem[];
  choice: Choice;
  rest: string[];
  personal: Personal;
  /** Ключи общей сцены ведущего (как есть; отсутствующие сейчас элементы не показываются). */
  spot: string[];
  spotBy: string | null;
  /** Зритель сам отказался следовать текущей общей сцене (закрыл доску, выбрал своё) — до следующей смены сцены ведущим. */
  spotIgnored: boolean;
  speaker: string | null;
  boardItem: boolean;
  openLarge: (key: string) => void;
  togglePin: (key: string) => void;
  unpin: (key: string) => void;
  resetPins: () => void;
  setLayout: (l: LayoutMode) => void;
  follow: () => void;
  ignoreSpot: () => void;
  applyServer: (st: StageState) => void;
  /** Руководитель: задать общую сцену (сервер проверит права и участников). */
  setSpot: (keys: string[]) => Promise<void>;
}

const read = (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* приватный режим: закрепления живут до перезагрузки */ } };

/**
 * Сцена встречи: что крупно у ЭТОГО зрителя. Личное (закрепления, режим) — в localStorage только для этой встречи; общая сцена ведущего — с сервера
 * (при входе, после переподключения канала событий и по событию stage_changed). Медиа здесь не трогаются: хук лишь выбирает ключи и порядок.
 */
export function useMeetingStage(o: { meetingId: string | undefined; sources: StageSource[]; boardOpen: boolean; canViewBoard: boolean; mobile: boolean }): MeetingStage {
  const { meetingId, sources, boardOpen, canViewBoard, mobile } = o;
  const [personal, setPersonal] = useState<Personal>(() => parsePersonal(null));
  const [server, setServer] = useState<StageState>({ items: [], by: null, at: null });
  const [ignoredAt, setIgnoredAt] = useState<number | null>(null);
  const sinceRef = useRef<Map<string, number>>(new Map());
  const tracker = useRef(new SpeakerTracker());
  const [speaker, setSpeaker] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!meetingId) return;
    setPersonal(parsePersonal(read(storageKey(meetingId))));       // перезагрузка страницы во время встречи — закрепления на месте
    let alive = true;
    api.getStage(meetingId).then((st) => { if (alive) setServer(st); }).catch(() => undefined);
    return () => { alive = false; };
  }, [meetingId]);
  const save = useCallback((p: Personal) => { setPersonal(p); if (meetingId) write(storageKey(meetingId), JSON.stringify(p)); }, [meetingId]);
  const personalRef = useRef(personal);
  personalRef.current = personal;
  const update = useCallback((fn: (p: Personal) => Personal) => save(fn(personalRef.current)), [save]);

  const spot = useMemo(() => server.items.map((i) => keyOf(i.type, i.identity)), [server]);
  const spotIgnored = ignoredAt !== null && ignoredAt === server.at;
  const activeSpot = spotIgnored ? [] : spot;
  const boardItem = canViewBoard && (boardOpen || activeSpot.includes("board"));

  // активный говорящий: себя крупно не ставим; повторная проверка — когда кандидат «дозреет»
  const speakingKey = sources.filter((s) => s.speaking && !s.local).map((s) => s.identity).join("|");
  const presentKey = sources.map((s) => s.identity).join("|");
  useEffect(() => {
    const now = performance.now();
    const cur = tracker.current.update(speakingKey ? speakingKey.split("|") : [], now, new Set(presentKey.split("|")));
    setSpeaker(cur);
    const wait = tracker.current.nextCheck(now);
    if (wait === null) return;
    const t = window.setTimeout(() => setTick((n) => n + 1), Math.max(50, wait + 20));
    return () => window.clearTimeout(t);
  }, [speakingKey, presentKey, tick]);

  const sig = sources.map((s) => `${s.identity}\u0001${s.name}\u0001${s.local ? 1 : 0}${s.screen ? 1 : 0}`).join("\u0002");
  const items = useMemo(() => {
    const keys = buildItems(sources, boardItem, new Map()).map((i) => i.key);
    sinceRef.current = trackSince(sinceRef.current, keys, Date.now());
    return buildItems(sources, boardItem, sinceRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, boardItem]);
  const choice = useMemo(() => chooseMain({ items, pins: personal.pins, spotlight: activeSpot, speaker, layout: personal.layout, mobile }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [items, personal, activeSpot.join("|"), speaker, mobile]);
  const rest = useMemo(() => stripOrder(items, choice.main), [items, choice]);

  const leaveGrid = (p: Personal): Personal => (p.layout === "grid" ? { ...p, layout: "auto" } : p);
  const openLarge = useCallback((key: string) => update((p) => leaveGrid({ ...p, pins: [key] })), [update]);
  const pin = useCallback((key: string) => update((p) => leaveGrid({ ...p, pins: togglePin(p.pins, key) })), [update]);
  const unpin = useCallback((key: string) => update((p) => ({ ...p, pins: p.pins.filter((k) => k !== key) })), [update]);
  const resetPins = useCallback(() => update((p) => ({ ...p, pins: [] })), [update]);
  const setLayout = useCallback((layout: LayoutMode) => update((p) => ({ ...p, layout })), [update]);
  const follow = useCallback(() => { setIgnoredAt(null); update((p) => ({ ...p, pins: [], layout: p.layout === "grid" ? "auto" : p.layout })); }, [update]);
  const ignoreSpot = useCallback(() => setIgnoredAt(server.at), [server.at]);
  const applyServer = useCallback((st: StageState) => setServer({ items: st.items ?? [], by: st.by ?? null, at: st.at ?? null }), []);
  const setSpot = useCallback(async (keys: string[]) => {
    if (!meetingId) return;
    const body = keys.map(parseKey).filter((x): x is NonNullable<typeof x> => !!x);
    applyServer(await api.setStage(meetingId, body));
  }, [meetingId, applyServer]);

  return { items, choice, rest, personal, spot, spotBy: server.by, spotIgnored, speaker, boardItem, openLarge, togglePin: pin, unpin, resetPins, setLayout, follow, ignoreSpot, applyServer, setSpot };
}
