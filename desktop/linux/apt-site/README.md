# Репозиторий APT Peregovorka для Linux

Отсюда Ubuntu и Debian получают приложение Peregovorka и его обновления (`apt update && apt upgrade`). Сайт: https://calleograph.github.io/peregovorka-apt/

**Установка** — инструкция на сайте и в [docs/LINUX.md](https://github.com/calleograph/peregovorka/blob/main/docs/LINUX.md) основного репозитория.

## Как устроено

| Что | Где |
| --- | --- |
| Подписанные индексы (`dists/stable/InRelease`, `Release`, `Release.gpg`, `main/binary-*/Packages*`) | этот репозиторий; подпись — ключом владельца на доверенной машине (`scripts/linux-apt/apt-publish.sh`), закрытого ключа здесь нет |
| Пакеты `.deb` | файлы выпусков `desktop-vX.Y.Z` основного репозитория; `pool.lock` — путь в `pool/`, SHA-256, размер и адрес каждого |
| Сайт | GitHub Pages собирает `.github/workflows/pages.yml`: скачивает пакеты, сверяет с `pool.lock` и подписанными индексами, проверяет подписи (`verify-repo.py`) и только потом выкладывает сайт целиком |
| Открытый ключ | `peregovorka-archive-keyring.gpg` (`.asc`), отпечаток — `FINGERPRINT` |

История публикаций — история этого репозитория: каждое состояние репозитория APT — отдельный коммит.

## Восстановить предыдущее состояние

`git revert <коммит>` (или `git revert HEAD`) и `git push` — workflow соберёт и проверит прежнее состояние и выложит его. Подписи индексов при этом прежние и действительны.
Пакеты прежних версий остаются в выпусках GitHub; установить определённую версию: `sudo apt install peregovorka=X.Y.Z --allow-downgrades`.
