import { describe, expect, it } from "vitest";
import { FreezeDetector, RateMeter, parseInbound, parseOutbound, parsePath } from "./rtcStats";

const rep = (...items: Record<string, unknown>[]) => new Map(items.map((i) => [i.id as string, i]));

describe("исходящий видеопоток (камера/экран)", () => {
  it("суммирует simulcast-слои, берёт разрешение верхнего, кодек, NACK/PLI, потери и RTT с удалённой стороны", () => {
    const r = parseOutbound(rep(
      { id: "c1", type: "codec", mimeType: "video/VP9" },
      { id: "o1", type: "outbound-rtp", kind: "video", codecId: "c1", frameWidth: 1920, frameHeight: 1080, framesPerSecond: 14.8, packetsSent: 1000, bytesSent: 500000, nackCount: 4, pliCount: 2, firCount: 0, framesEncoded: 300, qualityLimitationReason: "bandwidth" },
      { id: "o2", type: "outbound-rtp", kind: "video", codecId: "c1", frameWidth: 640, frameHeight: 360, framesPerSecond: 5, packetsSent: 200, bytesSent: 50000, nackCount: 1, pliCount: 0, firCount: 0, framesEncoded: 100, qualityLimitationReason: "none" },
      { id: "r1", type: "remote-inbound-rtp", packetsLost: 7, jitter: 0.012, roundTripTime: 0.031 },
    ).values());
    expect(r).toMatchObject({ codec: "VP9", width: 1920, height: 1080, fps: 14.8, packetsSent: 1200, nack: 5, pli: 2, framesEncoded: 400, packetsLost: 7, jitterMs: 12, rttMs: 31, limitReason: "bandwidth" });
  });
  it("без исходящего видео возвращает пусто", () => expect(parseOutbound([])).toEqual({}));
});

describe("входящий видеопоток", () => {
  it("кадры, потери, jitter, NACK/PLI", () => {
    const r = parseInbound(rep(
      { id: "c", type: "codec", mimeType: "video/VP8" },
      { id: "i", type: "inbound-rtp", kind: "video", codecId: "c", framesDecoded: 900, framesDropped: 3, framesPerSecond: 15, packetsReceived: 5000, packetsLost: 12, jitter: 0.004, nackCount: 9, pliCount: 1, frameWidth: 1280, frameHeight: 720 },
    ).values());
    expect(r).toMatchObject({ codec: "VP8", framesDecoded: 900, framesDropped: 3, fps: 15, packetsReceived: 5000, packetsLost: 12, jitterMs: 4, nack: 9, pli: 1 });
  });
});

describe("путь ICE", () => {
  it("тип локального/удалённого кандидата, протокол и RTT выбранной пары", () => {
    const p = parsePath(rep(
      { id: "t", type: "transport", selectedCandidatePairId: "p" },
      { id: "p", type: "candidate-pair", localCandidateId: "l", remoteCandidateId: "r", currentRoundTripTime: 0.018 },
      { id: "l", type: "local-candidate", candidateType: "host", protocol: "udp" },
      { id: "r", type: "remote-candidate", candidateType: "srflx", protocol: "udp" },
    ).values());
    expect(p).toEqual({ localType: "host", remoteType: "srflx", protocol: "udp", candidate: "udp/host→srflx", rttMs: 18 });
  });
  it("relay/tcp различимы — это признак «плохого» пути", () => {
    const p = parsePath(rep({ id: "p", type: "candidate-pair", nominated: true, localCandidateId: "l", remoteCandidateId: "r" }, { id: "l", candidateType: "relay", protocol: "tcp" }, { id: "r", candidateType: "host", protocol: "tcp" }).values());
    expect(p.candidate).toBe("tcp/relay→host");
  });
});

describe("битрейт и заморозка", () => {
  it("битрейт по приращению; сброс счётчика не даёт отрицательных значений", () => {
    const m = new RateMeter();
    expect(m.kbps("a", 0, 0)).toBeUndefined();
    expect(m.kbps("a", 250_000, 1000)).toBe(2000);
    expect(m.kbps("a", 10, 2000)).toBeUndefined();
  });
  it("заморозка определяется по неросту framesDecoded и снимается при возобновлении", () => {
    const d = new FreezeDetector(3);
    const seq = [100, 130, 130, 130, 130, 160].map((f) => d.update("s", f));
    expect(seq).toEqual([null, null, null, null, "frozen", "recovered"]);
    expect(d.update("s", undefined)).toBeNull();
  });
});
