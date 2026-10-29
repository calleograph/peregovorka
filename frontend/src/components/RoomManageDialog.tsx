import { useState } from "react";
import type { RoomManage } from "../api";
import { Modal } from "./Dialogs";
import RoomEditor from "./RoomEditor";

/** «Настройки комнаты» из самой комнаты: тот же редактор, что и в администрировании (components/RoomEditor.tsx). Администратор сервера видит и системные поля. */
export default function RoomManageDialog({ roomId, onClose, onSaved }: { roomId: string; onClose: () => void; onSaved?: (r: RoomManage) => void }) {
  const [name, setName] = useState("");
  return (
    <Modal title={name ? `Настройки комнаты «${name}»` : "Настройки комнаты"} onClose={onClose} wide>
      <RoomEditor roomId={roomId} onClose={onClose} onSaved={(r) => { setName(r.name); onSaved?.(r); }} />
    </Modal>
  );
}
