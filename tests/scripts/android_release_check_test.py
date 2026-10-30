"""Проверка совместимости обновления APK: правила сравнения и разбор вывода инструментов Android SDK (без самого SDK)."""
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location("arc", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "android_release_check.py"))
arc = importlib.util.module_from_spec(spec); spec.loader.exec_module(arc)

A = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99".replace(":", "")
CERT_A = ":".join(A[i:i + 2] for i in range(0, 64, 2)).upper()
CERT_B = "30:01:6B:BA" + ":00" * 28
base = {"package": "org.peregovorka.android", "version_code": 101, "version_name": "0.1.1", "certs": [CERT_A]}


def case(name, ok):
    if not ok:
        print("FAIL:", name); sys.exit(1)


case("badging разбирается", arc.parse_badging("package: name='org.peregovorka.android' versionCode='102' versionName='0.1.2' platformBuildVersionName='17'\nsdkVersion:'26'") == {"package": "org.peregovorka.android", "version_code": 102, "version_name": "0.1.2"})
case("сертификаты из apksigner", arc.parse_certs("Signer #1 certificate DN: CN=Android Debug\nSigner #1 certificate SHA-256 digest: 88557d3fe36678b54232125170e1070b45db2ea303b05c443234e81152a97416\n")[0].startswith("88:55:7D:3F"))
case("предыдущего нет — нормально", arc.compare(base, None)[0] == 0)
code, msgs = arc.compare({**base, "version_code": 102, "version_name": "0.1.2"}, base)
case("тот же ключ и versionCode вырос — обновление возможно", code == 0 and "возможно" in msgs[0])
code, msgs = arc.compare({**base, "version_code": 102, "certs": [CERT_B]}, base)
case("другой сертификат — обновление поверх невозможно, причина названа", code == 3 and "сертификат подписи отличается" in msgs[0] and "удалить" in msgs[0] and CERT_A in msgs[0] and CERT_B in msgs[0])
case("versionCode не вырос — ошибка", arc.compare({**base, "version_code": 101}, base)[0] == 1 and arc.compare({**base, "version_code": 100}, base)[0] == 1)
case("другой пакет — ошибка", arc.compare({**base, "package": "org.example.other", "version_code": 200}, base)[0] == 1)
case("повторная установка того же выпуска отклоняется как «не новее» (Android ставит её только поверх с -r, версию не понижает)", arc.compare(base, base)[0] == 1)
case("несколько подписывающих: достаточно совпадения набора", arc.compare({**base, "version_code": 102, "certs": [CERT_A]}, {**base, "certs": [CERT_A]})[0] == 0)
print("ok")
