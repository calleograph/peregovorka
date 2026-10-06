import { useCallback, useEffect, useState } from "react";
import { Room as LkRoom } from "livekit-client";

type Kind = "audioinput" | "audiooutput" | "videoinput";
const LABELS: Record<Kind, string> = { audioinput: "Микрофон", audiooutput: "Динамики", videoinput: "Камера" };

/** Выбор устройств во время встречи: микрофон, динамики (где поддерживается), камера. */
export default function DevicePanel({ room }: { room: LkRoom }) {
  const [devices, setDevices] = useState<Record<Kind, MediaDeviceInfo[]>>({ audioinput: [], audiooutput: [], videoinput: [] });
  const [active, setActive] = useState<Partial<Record<Kind, string>>>({});
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    const kinds: Kind[] = ["audioinput", "audiooutput", "videoinput"];
    const lists = await Promise.all(kinds.map((k) => LkRoom.getLocalDevices(k, false).catch(() => [] as MediaDeviceInfo[])));
    setDevices({ audioinput: lists[0], audiooutput: lists[1], videoinput: lists[2] });
    setActive(Object.fromEntries(kinds.map((k) => [k, room.getActiveDevice(k)])) as Partial<Record<Kind, string>>);
  }, [room]);

  useEffect(() => {
    void load();
    navigator.mediaDevices?.addEventListener?.("devicechange", load);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", load);
  }, [load]);

  const pick = async (kind: Kind, id: string) => {
    setErr("");
    try { await room.switchActiveDevice(kind, id); setActive((a) => ({ ...a, [kind]: id })); }
    catch { setErr("Не удалось переключить устройство."); }
  };

  const kinds = (Object.keys(LABELS) as Kind[]).filter((k) => devices[k].length > 0 && (k !== "audiooutput" || "setSinkId" in HTMLMediaElement.prototype));
  return (
    <details className="devices">
      <summary>Устройства</summary>
      <div className="cols">
        {kinds.map((k) => (
          <label key={k}>{LABELS[k]}
            <select value={active[k] ?? ""} onChange={(e) => pick(k, e.target.value)}>
              {devices[k].map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${LABELS[k]} ${i + 1}`}</option>)}
            </select>
          </label>
        ))}
      </div>
      {err && <div className="alert error">{err}</div>}
    </details>
  );
}
