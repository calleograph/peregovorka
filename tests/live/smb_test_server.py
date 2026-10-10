"""Настоящий SMB-сервер (impacket, SMB2) для проверки SmbStorage по сети без Windows-шары и Samba: python smb_test_server.py <каталог> [порт] [пользователь] [пароль].
Общий ресурс называется REC. Останавливается убийством процесса — так в испытаниях имитируется обрыв сети/выключение файлового сервера.
Ограничения: это реализация impacket, а не Windows/Samba — поведение блокировок, квот и удаления открытых файлов у настоящих серверов отличается; испытание не заменяет проверку на реальной шаре."""
import sys

from impacket import ntlm
from impacket.smbserver import SimpleSMBServer

# impacket отвечает на «создать файл» значением CreateAction из запроса (иногда 5), а по спецификации SMB2 допустимо 0–3: smbprotocol такой ответ отвергает.
from impacket.smbserver import SMB2Commands  # noqa: E402

_create = SMB2Commands.smb2Create


def _create_fixed(connId, smbServer, recvPacket):
    cmds, data, err = _create(connId, smbServer, recvPacket)
    for c in cmds:
        try:
            if c["CreateAction"] not in (0, 1, 2, 3):
                c["CreateAction"] = 2
        except Exception:  # noqa: BLE001
            pass
    if err == 0xC000000F:           # STATUS_NO_SUCH_FILE: настоящие серверы на «нет такого файла/каталога» отвечают OBJECT_NAME_NOT_FOUND (0xC0000034)
        err = 0xC0000034
    return cmds, data, err


SMB2Commands.smb2Create = staticmethod(_create_fixed)

root = sys.argv[1]
port = int(sys.argv[2]) if len(sys.argv) > 2 else 4450
user = sys.argv[3] if len(sys.argv) > 3 else "svc"
pw = sys.argv[4] if len(sys.argv) > 4 else "svc-pass"
srv = SimpleSMBServer(listenAddress="127.0.0.1", listenPort=port)
srv.addShare("REC", root, "")
srv.setSMB2Support(True)
srv.addCredential(user, 1000, "", ntlm.compute_nthash(pw).hex())
srv.setLogFile("")
print("smb ready", flush=True)
srv.start()
