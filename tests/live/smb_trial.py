"""Испытание настоящей SMB-шары для переноса записей (запускать на сервере или рабочей станции с доступом к шаре; backend не нужен).

    python tests/live/smb_trial.py --server files.corp.local --share Recordings --base Peregovorka-trial --user svc_peregovorka [--domain CORP] [--size-mb 200] [--outage]

Пароль спрашивается без эха (или переменная SMB_PASSWORD). Скрипт работает ТОЛЬКО в подкаталоге --base (создаёт его и удаляет за собой), чужих файлов не трогает.
Проверяется то, что нельзя проверить локально: подключение и авторизация, вложенные каталоги, запись и чтение большого файла потоком, диапазоны, сверка SHA-256,
подмена существующего файла без потери (rename-with-replace), свободное место тома (stat_volume), удаление. С ключом --outage скрипт на середине копирования просит
отключить сеть/выключить сервер и затем вернуть — проверяется, что обрыв даёт понятную ошибку, а не зависание, и что после возвращения всё продолжает работать.
Код возврата 0 — всё прошло; результат каждого шага печатается строкой OK/FAIL. Приложите вывод к отчёту об испытании."""
import argparse, getpass, hashlib, os, sys, tempfile, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from app.services.storage import SmbStorage, StorageError, StorageNotFound  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--server", required=True, help="имя или IP сервера, можно с портом host:port")
ap.add_argument("--share", required=True)
ap.add_argument("--base", default="Peregovorka-trial", help="подкаталог внутри ресурса для испытания")
ap.add_argument("--user", required=True)
ap.add_argument("--domain", default="")
ap.add_argument("--size-mb", type=int, default=100, help="размер большого тестового файла")
ap.add_argument("--outage", action="store_true", help="интерактивная проверка обрыва сети во время копирования")
a = ap.parse_args()
pw = os.environ.get("SMB_PASSWORD") or getpass.getpass("Пароль: ")
st = SmbStorage(a.server, a.share, a.base, a.user, pw, a.domain)
res: list[bool] = []


def step(name, fn, expect=None):
    t = time.time()
    try:
        out = fn()
        ok = True if expect is None else out == expect
        print(("OK   " if ok else "FAIL ") + f"{name}  [{time.time() - t:.1f} с]" + ("" if ok else f"  получено: {out!r}"), flush=True)
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"FAIL {name}  [{type(exc).__name__}: {str(exc)[:160]}]", flush=True)
    res.append(ok)


tmp = tempfile.mkdtemp(prefix="smb-trial-")
src, dst = os.path.join(tmp, "src.bin"), os.path.join(tmp, "dst.bin")
h = hashlib.sha256()
with open(src, "wb") as fh:
    for _ in range(a.size_mb):
        chunk = os.urandom(1 << 20)
        h.update(chunk)
        fh.write(chunk)
want = h.hexdigest()
size = a.size_mb << 20


def sha_remote(rel):
    d = hashlib.sha256()
    for chunk in st.read_range(rel, 0, size - 1):
        d.update(chunk)
    return d.hexdigest()


def _missing():
    try:
        st.size_of("no/such/file.bin")
    except StorageNotFound:
        return True
    except StorageError:
        return False
    return False


step("подключение и проверка ресурса (probe)", st.probe)
step("запись маленького файла во вложенный каталог", lambda: st.write_bytes("a/b/c/x.txt", b"hello") and True)
step("чтение и размер", lambda: (st.read_bytes("a/b/c/x.txt"), st.size_of("a/b/c/x.txt")), (b"hello", 5))
step("существование и список каталога", lambda: (st.exists("a/b/c/x.txt"), "x.txt" in st.list_dir("a/b/c")), (True, True))
step(f"копирование {a.size_mb} МиБ на шару (потоком, через временное имя)", lambda: st.copy_in("big/rec.bin", src) and True)
step("размер большого файла на шаре", lambda: st.size_of("big/rec.bin"), size)
step("SHA-256 файла на шаре совпал с источником", lambda: sha_remote("big/rec.bin"), want)
step("диапазон из середины файла совпал", lambda: b"".join(st.read_range("big/rec.bin", 1000, 2999)) == open(src, "rb").read(3000)[1000:3000], True)
step("повторное копирование поверх существующего (подмена без потери)", lambda: st.copy_in("big/rec.bin", src) and st.size_of("big/rec.bin"), size)
step("копирование обратно и сверка SHA-256", lambda: (st.copy_out("big/rec.bin", dst), hashlib.sha256(open(dst, "rb").read()).hexdigest())[1], want)
step("свободное место тома (stat_volume)", lambda: all(v > 0 for v in st.volume()), True)
step("отсутствующий файл → понятное «не найден», а не сбой", lambda: _missing(), True)


if a.outage:
    print("\n— ПРОВЕРКА ОБРЫВА: сейчас начнётся копирование. Когда увидите «ОТКЛЮЧИТЕ СЕТЬ», отключите сеть/выключите файловый сервер на 20–30 секунд, затем верните.")
    input("Нажмите Enter, чтобы начать… ")
    import threading

    box: dict = {}

    def run():
        t = time.time()
        try:
            st.copy_in("outage/rec.bin", src)
            box["r"] = "завершено без ошибки (обрыв не успел повлиять)"
        except Exception as exc:  # noqa: BLE001
            box["r"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        box["t"] = time.time() - t

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(1.5)
    print(">>> ОТКЛЮЧИТЕ СЕТЬ сейчас <<<", flush=True)
    th.join(timeout=300)
    print(f"копирование во время обрыва: {box.get('r', 'не завершилось за 300 с (зависание!)')} ({box.get('t', 0):.0f} с)")
    res.append("r" in box and isinstance(box.get("t"), float) and box["t"] < 300)
    input("Верните сеть и нажмите Enter… ")
    step("после возвращения сети: чтение работает без перезапуска", lambda: st.size_of("a/b/c/x.txt"), 5)
    step("после возвращения сети: повторное копирование проходит и сверяется", lambda: (st.copy_in("outage/rec.bin", src), sha_remote("outage/rec.bin"))[1], want)

try:
    st.delete_dir("")           # убираем за собой всё содержимое --base
except Exception as exc:  # noqa: BLE001
    print(f"Не удалось удалить каталог испытания ({a.base}): {exc}")
print(f"\nИТОГО: {sum(res)} из {len(res)}")
sys.exit(0 if all(res) else 1)
