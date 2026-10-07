import { describe, expect, it } from "vitest";
import type { WhiteboardPatch, WhiteboardState } from "../api";
import { BoardSync, EMPTY_XML, type BoardStatus } from "./whiteboardSync";

// ---------- подставные части: часы, сервер, редактор draw.io ----------
class Clock {
  now = 0; private seq = 0; private timers = new Map<number, { at: number; fn: () => void }>();
  set = (fn: () => void, ms: number) => { const id = ++this.seq; this.timers.set(id, { at: this.now + ms, fn }); return id; };
  clear = (h: unknown) => { this.timers.delete(h as number); };
  async advance(ms: number) {
    const end = this.now + ms;
    for (;;) {
      await flush();
      const due = [...this.timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.now = due[1].at; this.timers.delete(due[0]); due[1].fn();
    }
    this.now = end; await flush();
  }
}
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

class Server {
  seq = 0; tail: WhiteboardPatch[] = []; snap = { xml: null as string | null, seq: 0 }; clients: Client[] = []; failSend = false; holdBroadcast = false; held: Array<() => void> = [];
  state(): WhiteboardState {
    return { xml: this.snap.xml, seq: this.snap.seq, patches: this.tail.filter((p) => p.seq > this.snap.seq), active: true, used: false, shapes: 0, updated_at: null, updated_by: null };
  }
  async send(body: { patch: unknown; checksum: string | null; client_id: string }) {
    if (this.failSend) throw new Error("offline");
    const seq = ++this.seq;
    const ev = { seq, patch: body.patch, checksum: body.checksum, from: body.client_id, by: "x" };
    this.tail.push(ev);
    const deliver = () => this.clients.forEach((c) => c.sync.handleLive({ type: "whiteboard_patch", ...ev }));
    if (this.holdBroadcast) this.held.push(deliver); else deliver();
    return { seq };
  }
  async save(xml: string, seq: number) {
    if (seq < this.snap.seq) return { saved: false, seq: this.snap.seq };
    this.snap = { xml, seq };
    this.clients.forEach((c) => c.sync.handleLive({ type: "whiteboard_saved", seq }));
    return { saved: true, seq };
  }
}

/** Редактор: документ — множество «правок» (патчи коммутируют, как у draw.io); контрольная сумма — отсортированное содержимое. */
class Editor {
  doc: string[] = []; applied: string[] = []; xmlOf = (d: string[]) => `X:${JSON.stringify(d)}`;
  constructor(private emit: (m: Record<string, unknown>) => void) {}
  sum() { return [...this.doc].sort().join(","); }
  post = (m: Record<string, unknown>) => {
    queueMicrotask(() => {
      if (m.action === "load") { const x = String(m.xml); this.doc = x === EMPTY_XML ? [] : JSON.parse(x.slice(2)); this.emit({ event: "load" }); }
      else if (m.action === "patch") { this.doc.push(String(m.patch)); this.applied.push(String(m.patch)); this.emit({ event: "patch", checksum: this.sum(), checksumMismatch: !!m.checksum && m.checksum !== this.sum() }); }
      else if (m.action === "export") this.emit({ event: "export", format: m.format, xml: this.xmlOf(this.doc) });
    });
  };
  edit(v: string) { this.doc.push(v); this.emit({ event: "autosave", patch: v, checksum: this.sum(), xml: this.xmlOf(this.doc) }); }
}

class Client {
  editor: Editor; sync: BoardSync; statuses: BoardStatus[] = [];
  constructor(public id: string, server: Server, clock: Clock, opts: { readOnly?: boolean } = {}) {
    this.editor = new Editor((m) => this.sync.handleFrame(m));
    this.sync = new BoardSync({
      clientId: id, readOnly: opts.readOnly,
      fetchState: async () => server.state(), sendPatch: (b) => server.send(b), saveSnapshot: (x, s) => server.save(x, s),
      post: this.editor.post, onStatus: (s) => this.statuses.push(s), setTimer: clock.set, clearTimer: clock.clear, random: () => 0,
    });
    server.clients.push(this);
  }
  async open() { this.sync.handleFrame({ event: "init" }); await flush(); }
}

const setup = async (n = 2) => {
  const clock = new Clock(), server = new Server();
  const clients = Array.from({ length: n }, (_, i) => new Client(`c${i}`, server, clock));
  for (const c of clients) await c.open();
  return { clock, server, clients };
};
const sorted = (c: Client) => [...c.editor.doc].sort();

describe("BoardSync", () => {
  it("правки двух участников сходятся; свои патчи повторно не применяются", async () => {
    const { clients: [a, b], clock } = await setup();
    a.editor.edit("a1"); await flush();
    b.editor.edit("b1"); await flush();
    a.editor.edit("a2"); await clock.advance(100);
    expect(sorted(a)).toEqual(["a1", "a2", "b1"]);
    expect(sorted(b)).toEqual(["a1", "a2", "b1"]);
    expect(a.editor.applied).toEqual(["b1"]);          // чужие применены, свои — нет
    expect(b.editor.applied).toEqual(["a1", "a2"]);
  });

  it("патчи, пришедшие не по порядку, применяются строго по номерам", async () => {
    const { clients: [a, b], server, clock } = await setup();
    server.holdBroadcast = true;
    a.editor.edit("p1"); await flush(); a.editor.edit("p2"); await flush(); a.editor.edit("p3"); await flush();
    server.held.reverse().forEach((f) => f());           // доставка 3, 2, 1
    await clock.advance(100);
    expect(b.editor.applied).toEqual(["p1", "p2", "p3"]);
  });

  it("пропущенный номер → после ожидания схема перезагружается с сервера и сходится", async () => {
    const { clients: [a, b], server, clock } = await setup();
    a.editor.edit("x1"); await clock.advance(50);
    server.holdBroadcast = true;
    a.editor.edit("x2"); await flush();                  // этот патч до b не дойдёт
    server.held.length = 0;
    server.holdBroadcast = false;
    a.editor.edit("x3"); await flush();                  // b видит seq=3 при lastSeq=1 — пропуск
    expect(b.editor.applied).toEqual(["x1"]);
    await clock.advance(2000);
    expect(sorted(b)).toEqual(["x1", "x2", "x3"]);      // перезагрузка: снимка нет → пустая схема + хвост патчей
  });

  it("опоздавший получает снимок и только более новые патчи", async () => {
    const { clients: [a], server, clock } = await setup(1);
    a.editor.edit("s1"); a.editor.edit("s2"); await clock.advance(3000);
    expect(server.snap.seq).toBe(2);
    expect(JSON.parse((server.snap.xml as string).slice(2)).sort()).toEqual(["s1", "s2"]);
    a.editor.edit("s3"); await flush();
    const late = new Client("late", server, clock);
    await late.open();
    await clock.advance(50);
    expect(sorted(late)).toEqual(["s1", "s2", "s3"]);
    expect(late.editor.applied).toEqual(["s3"]);         // s1, s2 — из снимка; повторно не применялись
  });

  it("снимок сохраняет автор после паузы; если автор пропал — запасной сохраняющий (другой клиент)", async () => {
    const { clients: [a, b], server, clock } = await setup();
    a.editor.edit("k1"); await clock.advance(100);
    expect(server.snap.seq).toBe(0);                      // пауза ещё не прошла
    await clock.advance(2500);
    expect(server.snap.seq).toBe(1);

    // автор следующей правки «пропал» (вкладка закрыта/зависла): его таймеры не сработают, b сохранит снимок сам спустя больший срок
    a.sync.dispose();
    server.clients = server.clients.filter((c) => c !== a);
    const lost = new Client("ghost", server, new Clock()); await lost.open();
    lost.editor.edit("k2"); await flush();
    server.clients = server.clients.filter((c) => c !== lost);
    await clock.advance(100);
    expect(b.editor.applied).toContain("k2");
    expect(server.snap.seq).toBe(1);
    await clock.advance(10000);
    expect(server.snap.seq).toBe(2);
    expect(JSON.parse((server.snap.xml as string).slice(2)).sort()).toEqual(["k1", "k2"]);
  });

  it("устаревший снимок не затирает свежий (сервер)", async () => {
    const server = new Server();
    await server.save("X:[1,2]", 5);
    expect((await server.save("X:[1]", 3)).saved).toBe(false);
    expect(server.snap.seq).toBe(5);
  });

  it("расхождение контрольных сумм → перезагрузка; три подряд → состояние «расходится» и без бесконечных перезагрузок", async () => {
    const { clients: [a, b], clock } = await setup();
    b.sync.handleFrame({ event: "patch", checksumMismatch: true }); await clock.advance(50);
    expect(b.statuses.some((s) => s.phase === "loading")).toBe(true);
    expect(b.sync.current().phase).toBe("ready");
    b.sync.handleFrame({ event: "patch", checksumMismatch: true }); await clock.advance(50);
    b.sync.handleFrame({ event: "patch", checksumMismatch: true }); await clock.advance(50);
    expect(b.sync.current().phase).toBe("desync");
    expect(a.sync.current().phase).toBe("ready");
  });

  it("режим просмотра: правки редактора на сервер не уходят и снимки не пишутся", async () => {
    const clock = new Clock(), server = new Server();
    const v = new Client("viewer", server, clock, { readOnly: true });
    await v.open();
    v.editor.edit("zzz"); await clock.advance(10000);
    expect(server.seq).toBe(0);
    expect(server.snap.seq).toBe(0);
  });

  it("не удалось отправить правку — понятное состояние вместо молчаливого расхождения", async () => {
    const { clients: [a], server, clock } = await setup(1);
    server.failSend = true;
    a.editor.edit("lost"); await clock.advance(5000);
    expect(a.sync.current().phase).toBe("desync");
    expect(a.sync.current().message).toMatch(/не удалось отправить/);
  });

  it("экспорт XML: ответ редактора возвращается, при молчании редактора — null", async () => {
    const { clients: [a], clock } = await setup(1);
    a.editor.edit("e1");
    const r = await (async () => { const p = a.sync.requestExport("xml"); await flush(); return p; })();
    expect(JSON.parse(String(r?.xml).slice(2))).toEqual(["e1"]);
    a.editor.post = () => undefined;                     // редактор «завис»
    (a.sync as unknown as { d: { post: unknown } }).d.post = () => undefined;
    const p2 = a.sync.requestExport("xml");
    await clock.advance(5000);
    expect(await p2).toBeNull();
  });

  it("после dispose события игнорируются и снимок автора сохраняется по последнему XML", async () => {
    const { clients: [a], server, clock } = await setup(1);
    a.editor.edit("d1"); await clock.advance(100);
    a.sync.dispose();
    await flush();
    expect(server.snap.seq).toBe(1);                      // успели сохранить при закрытии
    a.sync.handleLive({ type: "whiteboard_patch", seq: 99, patch: "z" });
    await clock.advance(1000);
    expect(a.editor.applied).toEqual([]);
  });
});
