# Peregovorka для Linux

Приложение для Linux — тот же клиент, что и для Windows (`desktop/`: Electron, общая оболочка и страницы сервера): все разделы, несколько организаций,
встречи со звуком, видео и показом экрана, чаты и файлы, уведомления, трей. Отличается только встраиванием в систему: пакет `.deb`, обновления через APT,
связка ключей рабочего стола вместо DPAPI, показ экрана в Wayland через системный портал. Версия Linux совпадает с версией приложения для Windows
(`desktop/package.json`, выпуск `desktop-vX.Y.Z` содержит и установщики Windows, и пакеты Linux).

## Поддерживаемые системы

| Система | Архитектура | Состояние |
| --- | --- | --- |
| Ubuntu 24.04 LTS | x86_64 (amd64) | поддерживается |
| Ubuntu 22.04 LTS | x86_64 | поддерживается |
| Debian 13 (trixie) | x86_64 | поддерживается |
| Debian 12 (bookworm) | x86_64 | поддерживается |
| Linux Mint 21–22, Pop!_OS 22.04, Zorin, elementary и другие на основе Ubuntu 22.04+/Debian 12+ | x86_64 | должно работать (те же пакеты), отдельно не проверялось |
| ARM64 (aarch64) | — | пакет собирается и проверяется в CI; в репозиторий и в `install.sh` не включён, пока не проверен звонок на настоящем устройстве ARM |

Рабочий стол: GNOME, KDE Plasma, Xfce, Cinnamon, MATE — X11 и Wayland. Что именно проверено — в разделе «Что проверено».

## Установка

### Одной командой (рекомендуется)

```bash
curl -fsSL https://calleograph.github.io/peregovorka-apt/install.sh | sudo sh
```

Сценарий проверяет систему и архитектуру, ставит недостающие `curl` и корневые сертификаты, скачивает открытый ключ репозитория и сверяет его
отпечаток (`48A7 5B62 BFA8 17D7 AAD3 E84E 2B00 ADE5 127C 5DF9`) и SHA-256, кладёт ключ в `/usr/share/keyrings/peregovorka-archive-keyring.gpg`,
описывает источник `/etc/apt/sources.list.d/peregovorka.sources` с `Signed-By` (ключ действует только для этого репозитория) и ставит пакет `peregovorka`.

**Debian без sudo.** В Debian `sudo` по умолчанию не настроен: выполните `su -` и затем `curl -fsSL https://calleograph.github.io/peregovorka-apt/install.sh | sh`.

### Пакетом .deb

Скачайте `peregovorka_X.Y.Z_amd64.deb` со [страницы выпусков](https://github.com/calleograph/peregovorka/releases?q=desktop-v) (контрольные суммы — `SHA256SUMS-linux.txt`) и:

```bash
sudo apt install ./peregovorka_X.Y.Z_amd64.deb
```

При первой установке пакет сам подключает репозиторий APT (так дальнейшие обновления придут через `apt upgrade`). Отключить — до установки:
`echo PEREGOVORKA_REPO=off | sudo tee /etc/default/peregovorka`.

### Вручную, с проверкой отпечатка

```bash
curl -fsSLo /tmp/peregovorka.gpg https://calleograph.github.io/peregovorka-apt/peregovorka-archive-keyring.gpg
gpg --show-keys /tmp/peregovorka.gpg          # 48A75B62BFA817D7AAD3E84E2B00ADE5127C5DF9 — иначе не продолжайте
sudo install -m 0644 /tmp/peregovorka.gpg /usr/share/keyrings/peregovorka-archive-keyring.gpg
sudo tee /etc/apt/sources.list.d/peregovorka.sources <<'EOF'
Types: deb
URIs: https://calleograph.github.io/peregovorka-apt
Suites: stable
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/peregovorka-archive-keyring.gpg
EOF
sudo apt update && sudo apt install peregovorka
```

После установки: «Peregovorka» в меню приложений или команда `peregovorka`. Ссылки `peregovorka://…` (из писем и календаря) открываются в приложении.

## Обновление

- `sudo apt update && sudo apt upgrade` или графический менеджер обновлений («Обновление программ» Ubuntu, GNOME Software, Discover) — пакет приходит из репозитория Peregovorka вместе с системными обновлениями.
- Если приложение было запущено, APT заменяет его файлы, а работает прежняя версия. Приложение замечает это (раз в минуту сверяет `resources/package-version`), присылает уведомление
  «Peregovorka обновлена» и показывает в «Настройки → Обновления» кнопку **«Перезапустить»**. Во время встречи и показа экрана перезапуск недоступен.
- Своего механизма замены файлов в Linux у приложения нет (он конфликтовал бы с dpkg): «Настройки → Обновления» показывают только, что обновления устанавливает система.
- **Автоматически** (`unattended-upgrades`) — только если администратор разрешит источник:

```bash
echo 'Unattended-Upgrade::Origins-Pattern:: "origin=Peregovorka,suite=stable";' | sudo tee /etc/apt/apt.conf.d/52peregovorka-unattended
```

Без этой строки `unattended-upgrades` обновления Peregovorka не трогает (проверено: см. «Что проверено»).

## Удаление

| Что | Команда |
| --- | --- |
| Удалить приложение (репозиторий остаётся — можно поставить снова `sudo apt install peregovorka`) | `sudo apt remove peregovorka` |
| Удалить приложение, репозиторий и ключ | `sudo apt purge peregovorka` |
| Удалить только репозиторий (приложение остаётся без обновлений) | `sudo rm /etc/apt/sources.list.d/peregovorka.sources /usr/share/keyrings/peregovorka-archive-keyring.gpg && sudo apt update` |
| Удалить свои данные (организации, настройки, журналы; сохранённый вход — в связке ключей, запись «Peregovorka Safe Storage») | `rm -rf ~/.config/Peregovorka ~/.config/autostart/peregovorka.desktop` |

Пакет данные пользователей не удаляет никогда.

## Восстановление после ошибок

| Ситуация | Что делать |
| --- | --- |
| Загрузка пакета оборвалась | ничего не сломано: APT не ставит неполный файл (сверяет размер и SHA-256), установлена прежняя версия. Повторите `sudo apt upgrade` |
| Установка прервалась (выключили питание, закрыли терминал) | `sudo dpkg --configure -a && sudo apt -f install` |
| Новая версия работает хуже прежней | `sudo apt install peregovorka=X.Y.Z --allow-downgrades` (в репозитории — четыре последних версии; старые — в выпусках GitHub, ставятся как файл .deb). Закрепить версию: `sudo apt-mark hold peregovorka`, снять — `sudo apt-mark unhold peregovorka` |
| `apt update`: «NO_PUBKEY», «The following signatures couldn't be verified», «is not signed» | ключ репозитория отсутствует или устарел: переустановите ключ (раздел «Вручную»), сверив отпечаток. **Не отключайте проверку подписи** (`trusted=yes`, `--allow-unauthenticated`) |
| `apt update`: «Hash Sum mismatch» | индекс скачался не целиком или подменён по дороге (прокси с кэшем): повторите позже; через корпоративный прокси — сбросьте его кэш для `calleograph.github.io` |
| Приложение не запускается после обновления | `peregovorka --debug-log` в терминале; журнал `~/.config/Peregovorka/logs/main.log`; «Диагностика» ниже |

## Проверка подписей

APT проверяет подпись `InRelease` при каждом `apt update` ключом из `/usr/share/keyrings/peregovorka-archive-keyring.gpg` (поле `Signed-By` источника):
никакой другой ключ системы этот репозиторий подписать не может, и этот ключ не действует для других репозиториев. Вручную:

```bash
gpg --show-keys /usr/share/keyrings/peregovorka-archive-keyring.gpg     # отпечаток 48A75B62BFA817D7AAD3E84E2B00ADE5127C5DF9, RSA 4096, до 2031-11-02
curl -fsSLO https://calleograph.github.io/peregovorka-apt/dists/stable/InRelease
gpgv --keyring /usr/share/keyrings/peregovorka-archive-keyring.gpg InRelease   # «Good signature»
```

Каждый пакет сверяется APT по SHA-256 из подписанного индекса `Packages`. Пакеты .deb в выпусках GitHub — с `SHA256SUMS-linux.txt`.

## Диагностика

| Признак | Причина и решение |
| --- | --- |
| Окно не появляется, в терминале «No usable sandbox» / «SUID sandbox helper binary was found, but is not configured correctly» | права помощника песочницы сброшены: `sudo chown root:root /opt/Peregovorka/chrome-sandbox && sudo chmod 4755 /opt/Peregovorka/chrome-sandbox` (или переустановите пакет). Ubuntu 24.04+: профиль AppArmor `/etc/apparmor.d/peregovorka` должен быть загружен — `sudo aa-status | grep peregovorka`, загрузить: `sudo apparmor_parser -r /etc/apparmor.d/peregovorka` |
| Вход не сохраняется между запусками | нет связки ключей рабочего стола (Secret Service: GNOME Keyring, KWallet). «Диагностика» → «Хранилище входа: недоступно». В минимальных средах (i3, sway) поставьте и запустите `gnome-keyring`. Открытым текстом вход не хранится никогда |
| Нет значка в трее (GNOME) | в GNOME нет области значков: включите расширение «AppIndicator and KStatusNotifierItem Support» (в Ubuntu — включено) или выберите «Кнопка «Закрыть» → Выйти из приложения» и автозапуск «С открытым окном». Повторный запуск приложения всегда показывает его окно |
| Показ экрана в Wayland: пустой список или ошибка | нужны `xdg-desktop-portal` и портал вашего рабочего стола (`xdg-desktop-portal-gnome`, `-kde`, `-wlr` для sway/Hyprland) и PipeWire. Экран или окно выбирается в системном окне портала; своего окна выбора в Wayland нет. Звук компьютера при показе экрана в Linux не передаётся |
| Показ экрана в X11 | своё окно выбора с миниатюрами экранов и окон (как в Windows) |
| «Сертификат не доверен» | Chromium в Linux доверяет встроенному списку корневых центров и хранилищу NSS пользователя, а не `/etc/ssl/certs`. Корневой сертификат центра сертификации организации: `certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "Корпоративный ЦС" -i ca.crt` (пакет `libnss3-tools`) — для каждого пользователя, например сценарием входа |
| Нет звука или микрофона | «Настройки → Звук и видео»: что видит система, проверка микрофона; звуковой сервер — `systemctl --user status pipewire wireplumber` (или `pulseaudio`), устройства — `pavucontrol` |
| Иное | `peregovorka --diagnostics=отчёт.json` (версии, организации, устройства, без переписки), журнал `~/.config/Peregovorka/logs/main.log`, `peregovorka --debug-log` |

## Для администраторов

### Политики

Файл `/etc/peregovorka/policy.json` (создаёт администратор; изменить может только root, поэтому пользователь политику не обходит). Имена — как у
параметров групповой политики Windows (`docs/DESKTOP_ADMIN.md`), регистр не важен. Строки — в кавычках, флаги — 0/1.

```json
{
  "Servers": "Переговорки|https://meet.example.org",
  "AllowAddServers": 0,
  "AllowedDomains": "*.example.org",
  "Autostart": "tray",
  "CloseAction": "tray",
  "Notifications": 1,
  "HideNotificationContent": 0,
  "ProxyMode": "system"
}
```

`Servers` — организации «Название|адрес» через «;»; `AllowedDomains` — разрешённые адреса серверов; `Autostart` — off/tray/window; `CloseAction` — tray/quit/ask;
`ProxyMode` — system/direct/manual/pac (+ `ProxyServer`, `ProxyBypass`, `ProxyPac`); `AllowDevTools` — инструменты разработчика.
`UpdateMode` и `UpdateUrl` в Linux не действуют: обновления — через APT.

### Развёртывание

- **Много компьютеров:** тот же репозиторий в настройках управления конфигурацией (Ansible `apt_repository`/`deb822_repository` + `apt`, Puppet, Salt) с ключом из
  `desktop/linux/keyring/peregovorka-archive-keyring.gpg` основного репозитория; `/etc/default/peregovorka` с `PEREGOVORKA_REPO=off`, если источник описывает сама система управления.
- **Без доступа в интернет:** зеркало репозитория — каталог сайта целиком (`dists/`, `pool/`, ключ) на внутреннем веб-сервере; подпись `InRelease` сохраняется,
  клиенты проверяют её тем же ключом. Например `wget --mirror --no-parent https://calleograph.github.io/peregovorka-apt/` по расписанию; в источнике — адрес зеркала.
- **Автозапуск** — политикой `Autostart` или самим пользователем (`~/.config/autostart/peregovorka.desktop`).

### Что ставит пакет

`/opt/Peregovorka` (программа; `chrome-sandbox` — setuid root, помощник песочницы Chromium), `/usr/bin/peregovorka`, `/usr/share/applications/peregovorka.desktop`
(меню, ссылки `peregovorka://`), значки `hicolor`, `/usr/share/metainfo/` (каталоги программ), `/usr/share/man/man1/peregovorka.1`. Сценарии пакета:
профиль AppArmor `/etc/apparmor.d/peregovorka` (только при AppArmor 4 — Ubuntu 24.04+, Debian 13: разрешает песочнице пространства имён пользователя),
ключ репозитория (обновляется с каждой версией пакета) и при первой установке — источник APT. Зависимости — библиотеки, с которыми связана программа
(GTK 3, NSS, ALSA, GBM, X11/xkbcommon, libsecret, libnotify, xdg-utils); рекомендуемые — PipeWire и XDG Desktop Portal (показ экрана в Wayland), Mesa.

## Как устроены сборка, репозиторий и ключ (для разработчиков и владельца)

| Шаг | Где | Кто |
| --- | --- | --- |
| Программа и пакет `.deb` (amd64, arm64), lintian, SHA-256 | `.github/workflows/linux.yml` → `desktop/linux/build-deb.sh` | CI |
| Установка, запуск, обновление поверх предыдущего выпуска, удаление в Ubuntu 22.04/24.04 и Debian 12/13; запуск на настоящих ВМ (песочница, AppArmor) | `linux.yml` (`install`, `vm`), `desktop/linux/test/deb-check.sh` | CI |
| Инструменты репозитория и настоящий apt (одноразовый ключ CI) | `linux.yml` (`apt-repo`) | CI |
| Пакеты в выпуске `desktop-vX.Y.Z` | `linux.yml` (`release`) | CI на теге |
| Подписанные индексы, публикация на GitHub Pages | `scripts/linux-apt/apt-publish.sh` → репозиторий [peregovorka-apt](https://github.com/calleograph/peregovorka-apt) | владелец, на своей машине |

**Выпуск:** `cd desktop && npm run release -- X.Y.Z --push` (как для Windows) → CI собирает и проверяет пакеты и добавляет их в выпуск → владелец запускает
`scripts/linux-apt/apt-publish.sh` (`--dry-run` — всё, кроме отправки). Публикация: пакеты из выпусков (по 4 последних версии каждой архитектуры) сверяются
с `SHA256SUMS-linux.txt` → индексы (`apt-ftparchive`, `make-repo.sh`) → подпись (`sign-repo.sh`) → проверка (`verify-repo.py`: подписи, хэши, полнота) →
установка настоящим apt в чистой Ubuntu → отправка индексов и `pool.lock` в `peregovorka-apt` → GitHub Pages скачивает пакеты, ещё раз всё проверяет
и только тогда заменяет сайт целиком. Неполный или повреждённый индекс не публикуется ни на одном шаге.

**Почему так.** Пакеты — десятки мегабайт на версию; в истории Git сайта они быстро превысили бы ограничения GitHub (100 МБ на файл, 1 ГБ на сайт Pages),
поэтому в Git сайта — только подписанные индексы, а пакеты — в выпусках GitHub (история хранится там). `apt-ftparchive` (Debian, пакет apt-utils)
не хранит состояния: репозиторий целиком воспроизводится из выпусков — нет базы данных, которую можно потерять (как у aptly или reprepro).
`Valid-Until` в `Release` не ставится: сайт статический, и если владелец не переподпишет индексы вовремя, у всех перестал бы работать `apt update`.

**Восстановить прежнее состояние репозитория:** в `peregovorka-apt` — `git revert HEAD && git push` (подписи прежних индексов действительны).

**Ключ.** RSA 4096, только подпись, до 2031-11-02, отпечаток `48A75B62BFA817D7AAD3E84E2B00ADE5127C5DF9`. Закрытая часть — у владельца,
`.owner-private/linux-apt-signing/` (исключён из Git, Docker и пакетов; в CI, выпусках и журналах не появляется). Там же — копия каталога GnuPG,
экспорт закрытого ключа (защищён парольной фразой), парольная фраза отдельным файлом, сертификат отзыва, отпечаток, контрольные суммы и инструкция восстановления.

**Смена ключа (с переходным периодом).**
1. Создать новый ключ (как первый: `gpg --quick-gen-key … rsa4096 sign 5y` в отдельном каталоге `.owner-private/linux-apt-signing-<год>/`).
2. Положить в `desktop/linux/keyring/peregovorka-archive-keyring.gpg` **оба** открытых ключа (`gpg --export старый новый`), обновить отпечаток и SHA-256 в `install.sh`
   и выпустить новую версию приложения: при обновлении пакет заменит файл ключей у всех пользователей.
3. Несколько месяцев подписывать индексы обоими ключами: `SIGNING_KEYS="<старый> <новый>" scripts/linux-apt/apt-publish.sh` (`sign-repo.sh` ставит обе подписи;
   apt принимает индекс, если его ключу известна хотя бы одна).
4. Затем подписывать только новым, а следующей версией пакета убрать старый ключ из файла ключей.
Срок действия ключа продлевается без смены ключа: `gpg --quick-set-expire <отпечаток> 3y` в каталоге GnuPG владельца, затем выпуск пакета с обновлённым открытым ключом.

## Первый выпуск (владелец)

Сайт репозитория — отдельный публичный репозиторий `calleograph/peregovorka-apt` на GitHub Pages; создаёт его владелец один раз. До этого пакет Linux не выпускается:
при установке он подключает источник APT, и без сайта `apt update` у пользователей сообщал бы об ошибке.

```bash
scripts/linux-apt/bootstrap-apt-repo.sh                  # 1. репозиторий сайта и GitHub Pages (из GitHub Actions)
cd desktop && DISPLAY=:99 npm run release -- 1.5.0 --push # 2. выпуск клиента: Windows и Linux (CI собирает, проверяет и кладёт пакеты в выпуск)
cd .. && scripts/linux-apt/apt-publish.sh                # 3. после зелёного CI: подписанный репозиторий APT на GitHub Pages
```

## Что проверено

| Проверка | Как | Результат |
| --- | --- | --- |
| Установка (только Depends), запуск обычным пользователем с песочницей, ссылки `peregovorka://`, обновление поверх, remove, повторная установка, purge, сохранность настроек | `deb-check.sh` в Ubuntu 22.04/24.04, Debian 12/13 (контейнеры) | пройдено на всех четырёх |
| Репозиторий: установка `install.sh`, отказ подменённому индексу и чужой подписи, `apt update && apt upgrade` с оборванной загрузкой, откат на прежнюю версию, `unattended-upgrades` только при разрешённом источнике, purge | `apt-e2e.sh` в тех же четырёх системах | пройдено |
| Установленный из репозитория пакет против живого сервера (HTTPS, внутренний центр сертификации): мастер, вход, встреча, звук и видео в обе стороны с браузером, показ экрана (X11), полноэкранный режим и Esc, чат и файл, буфер обмена, сворачивание (индикатор встречи), уведомление о личном сообщении, трей (StatusNotifierItem), автозапуск, «О программе», «Обновления», связка ключей, SIGTERM и SIGKILL с сохранением входа и настроек, потеря и возврат сети, `apt upgrade` под работающей программой и «Перезапустить» в новую версию | `tests/live/linux_pkg_e2e.py` | 41 из 41 на Ubuntu 22.04, 24.04, Debian 12, 13 |
| Wayland: родной клиент Wayland, app_id, вход, встреча, камера; показ экрана — запрос через портал ScreenCast (своего окна выбора нет), при сбое портала — сообщение с причиной | `tests/live/linux_wayland_e2e.py` (Debian 13, sway без экрана, PipeWire, xdg-desktop-portal-wlr) | 10 из 10; **сами кадры экрана в Wayland не проверены**: композитор без видеокарты не отдаёт их порталу |
| Экраны 1280×720 @100 %, 1920×1080 @150 %, 2560×1440 @200 % | `tests/live/linux_scaling.py` | без прокрутки, кнопки в окне, масштаб применён |
| Настоящие ВМ GitHub (Ubuntu 22.04, 24.04, 24.04 ARM): установка apt, AppArmor, песочница, запуск | `linux.yml` (`vm`) | см. HISTORY |
| **Не проверено** | настоящие компьютеры с GNOME/KDE/Xfce (в испытаниях — оконный менеджер openbox и sway), настоящие камера, микрофон и наушники (в испытаниях — поддельные устройства Chromium), кадры показа экрана в Wayland и системное окно портала с выбором человеком, экраны HiDPI на настоящем оборудовании, звонок на ARM64, перезагрузка настоящей системы (в испытаниях — повторный запуск программы) | — |
