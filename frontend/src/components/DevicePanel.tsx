import { useCallback, useEffect, useState } from "react";
import { Room as LkRoom } from "livekit-client";
import { reportEvent } from "../diagnostics";
import { describeMediaError } from "../mediaErrors";

type Kind = "audioinput" | "audiooutput" | "videoinput";
const LABELS: Record<Kind, string> = { audioinput: "Микрофон", audiooutput: "Динамики", videoinput: "Камера" };
const ACTION: Record<Kind, "mic" | "camera" | "device"> = { audioinput: "mic", videoinput: "camera", audiooutput: "device" };

/** Выбор устройств во время встречи: микрофон, динамики (где поддерживается), камера. Ошибка показывается у самого списка. */
export default function DevicePanel({ room }: { room: LkRoom }) {
  const [devices, setDevices] = useState<Record<Kind, MediaDeviceInfo[]>>({ audioinput: [], audiooutput: [], videoinput: [] });
  const [active, setActive] = useState<Partial<Record<Kind, string>>>({});
  const [errs, setErrs] = useState<Partial<Record<Kind, string>>>({});

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
    setErrs((e) => ({ ...e, [kind]: undefined }));
    try { await room.switchActiveDevice(kind, id); setActive((a) => ({ ...a, [kind]: id })); }
    catch (e) {
      const info = describeMediaError(e, ACTION[kind]);
      setErrs((x) => ({ ...x, [kind]: `Не удалось переключить «${LABELS[kind].toLowerCase()}»: ${info.message}` }));
      reportEvent("device_error", { reason: info.reason, detail: `switch ${kind}: ${String((e as Error)?.message ?? e)}` });
    }
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
            {errs[k] && <span className="field-err" role="alert">{errs[k]}</span>}
          </label>
        ))}
      </div>
    </details>
  );
}
