#!/usr/bin/env python3
"""Проверка готового репозитория APT Peregovorka перед публикацией (и ещё раз на стороне GitHub Pages перед выкладкой).

    python3 verify-repo.py <каталог-сайта> <keyring.gpg> [--expect-fingerprint FPR] [--require-version X.Y.Z]

Неполный или повреждённый индекс не публикуется: код 1 при любой ошибке.
  * InRelease и Release.gpg — подписи ключом из keyring (gpgv), подписанный текст InRelease совпадает с Release байт в байт;
  * каждый файл из Release (MD5Sum/SHA256) есть, размер и SHA-256 совпадают; копии by-hash совпадают с оригиналами;
  * каждый пакет из Packages есть в pool, размер и SHA-256 совпадают, архитектура — заявленная; в pool нет лишних пакетов;
  * в сайте нет закрытых ключей.
Только стандартная библиотека Python и gpgv.
"""
import gzip, hashlib, lzma, os, re, subprocess, sys, tempfile


def deb822(text):
    """Абзацы «Поле: значение» (с продолжениями строк) → список словарей."""
    out, cur, key = [], {}, None
    for line in text.splitlines():
        if not line.strip():
            if cur: out.append(cur); cur, key = {}, None
            continue
        if line[0] in " \t" and key:
            cur[key] += "\n" + line.strip()
        else:
            key, _, val = line.partition(":")
            cur[key] = val.strip()
    if cur: out.append(cur)
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def main():
    args = sys.argv[1:]
    site, keyring = args[0], os.path.abspath(args[1])
    fpr = args[args.index("--expect-fingerprint") + 1] if "--expect-fingerprint" in args else None
    need = args[args.index("--require-version") + 1] if "--require-version" in args else None
    errs = []
    st = os.path.join(site, "dists", "stable")
    rel_path, inrel, relgpg = (os.path.join(st, n) for n in ("Release", "InRelease", "Release.gpg"))
    for p in (rel_path, inrel, relgpg):
        if not os.path.isfile(p): print(f"ОШИБКА: нет {p}"); return 1

    # подписи: gpgv с ключами из keyring (и только ими)
    for what, cmd in (("InRelease", ["gpgv", "--status-fd", "1", "--keyring", keyring, inrel]),
                      ("Release.gpg", ["gpgv", "--status-fd", "1", "--keyring", keyring, relgpg, rel_path])):
        r = subprocess.run(cmd, capture_output=True, text=True)
        good = re.findall(r"^\[GNUPG:\] VALIDSIG ([0-9A-F]{40})", r.stdout, re.M)
        if r.returncode != 0 or not good: errs.append(f"подпись {what} не прошла проверку: {r.stderr.strip()[-300:]}")
        elif fpr and fpr not in good: errs.append(f"{what} подписан не ключом {fpr}: {good}")
    with tempfile.TemporaryDirectory() as t:
        out = os.path.join(t, "Release")
        subprocess.run(["gpgv", "--keyring", keyring, "--output", out, inrel], capture_output=True)
        if not os.path.isfile(out) or open(out, "rb").read() != open(rel_path, "rb").read():
            errs.append("текст внутри InRelease не совпадает с Release")

    rel = deb822(open(rel_path, encoding="utf-8").read())[0]
    for f in ("Origin", "Label", "Suite", "Codename", "Architectures", "Components", "Date", "SHA256"):
        if f not in rel: errs.append(f"в Release нет поля {f}")
    if rel.get("Suite") != "stable" or rel.get("Components") != "main": errs.append(f"Suite/Components: {rel.get('Suite')}/{rel.get('Components')}")
    archs = rel.get("Architectures", "").split()
    listed = {}
    for line in rel.get("SHA256", "").splitlines():
        parts = line.split()
        if len(parts) == 3: listed[parts[2]] = (parts[0], int(parts[1]))
    for name, (h, size) in listed.items():
        p = os.path.join(st, name)
        if not os.path.isfile(p): errs.append(f"в Release указан, но нет файла {name}"); continue
        if os.path.getsize(p) != size or sha256(p) != h: errs.append(f"{name}: размер или SHA-256 не совпадают с Release")
        if rel.get("Acquire-By-Hash") == "yes" and "/binary-" in name and os.path.basename(name).startswith("Packages"):
            bh = os.path.join(os.path.dirname(p), "by-hash", "SHA256", h)
            if not os.path.isfile(bh) or sha256(bh) != h: errs.append(f"нет копии by-hash для {name}")

    pool_seen = set()
    total = 0
    for a in archs:
        pk = f"main/binary-{a}/Packages"
        for comp in ("", ".gz", ".xz"):
            if pk + comp not in listed: errs.append(f"в Release нет {pk}{comp}")
        try:
            raw = open(os.path.join(st, pk), "rb").read()
        except OSError:
            errs.append(f"нет {pk}"); continue
        if gzip.decompress(open(os.path.join(st, pk + ".gz"), "rb").read()) != raw or lzma.decompress(open(os.path.join(st, pk + ".xz"), "rb").read()) != raw:
            errs.append(f"{pk}.gz/.xz не совпадают с {pk}")
        entries = deb822(raw.decode("utf-8"))
        if not entries: errs.append(f"{pk} пуст")
        for e in entries:
            total += 1
            fn = e.get("Filename", "")
            if e.get("Package") != "peregovorka" or e.get("Architecture") != a or not re.fullmatch(r"pool/main/p/peregovorka/peregovorka_[0-9][\w.~+-]*_" + a + r"\.deb", fn):
                errs.append(f"{pk}: неожиданная запись {e.get('Package')} {e.get('Version')} {e.get('Architecture')} {fn}"); continue
            p = os.path.join(site, fn)
            pool_seen.add(os.path.normpath(p))
            if not os.path.isfile(p): errs.append(f"нет пакета {fn}"); continue
            if os.path.getsize(p) != int(e.get("Size", -1)) or sha256(p) != e.get("SHA256"): errs.append(f"{fn}: размер или SHA-256 не совпадают с Packages")
            if need and e.get("Version") == need: need = None
    if need: errs.append(f"в репозитории нет версии {need}")
    pool = os.path.join(site, "pool")
    for d, _, files in os.walk(pool):
        for f in files:
            if os.path.normpath(os.path.join(d, f)) not in pool_seen: errs.append(f"в pool лишний файл {os.path.relpath(os.path.join(d, f), site)}")
    for d, _, files in os.walk(site):
        if "/.git" in d: continue
        for f in files:
            p = os.path.join(d, f)
            if f.endswith(".deb") or os.path.getsize(p) > 5_000_000: continue
            if re.search(rb"-----BEGIN (PGP |RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY", open(p, "rb").read()): errs.append(f"закрытый ключ в {os.path.relpath(p, site)}")

    if errs:
        for e in errs: print("ОШИБКА:", e)
        return 1
    print(f"OK: подписи InRelease и Release.gpg верны, индексов {len(listed)}, пакетов {total} ({', '.join(archs)}), pool совпадает с индексом")
    return 0


if __name__ == "__main__":
    sys.exit(main())
