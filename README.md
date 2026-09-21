# vdlbot — телеграм-бот для скачивания видео

Принимает ссылку на видео, предлагает качество кнопками и присылает готовый файл.
Под капотом — [yt-dlp](https://github.com/yt-dlp/yt-dlp) и ffmpeg, поэтому понимает
большинство видеосайтов: YouTube, Instagram, TikTok, VK, Coub, Reddit, X, Rutube,
Vimeo, Twitch, SoundCloud и ещё около тысячи площадок.

Бот бесплатный. Есть добровольные донаты через Telegram Stars — платных функций нет.

## Что умеет

- Универсальный режим: принимает ссылку с любого сайта, который умеет yt-dlp
- Выбор качества: 1080p / 720p / 480p / 360p и 🎵 только аудио (mp3 192 kbps)
- Автоматическое сжатие ffmpeg'ом, если файл не влезает в лимит Telegram (по умолчанию 49 МБ)
- Работа в группах: реагирует только на сообщения со ссылками, отвечает реплаем
- Очередь загрузок с ограничением параллелизма и антиспам-кулдаун на пользователя
- Опциональный белый список пользователей (`ALLOWED_USERS`)
- Опциональный локальный Bot API server — файлы до 2 ГБ вместо 50 МБ
- Добровольные донаты в Telegram Stars + ненавязчивая плашка раз в N загрузок

## Быстрый старт (Docker)

```bash
git clone <repo> vdlbot && cd vdlbot
cp .env.example .env
nano .env                 # вписать BOT_TOKEN от @BotFather
docker compose up -d --build
docker compose logs -f
```

## Быстрый старт (без Docker)

Нужны Python 3.11+ и ffmpeg (`apt install ffmpeg`).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env
python -m bot.main
```

### Автозапуск через systemd

```bash
sudo useradd -r -s /usr/sbin/nologin -d /opt/vdlbot vdlbot
sudo mkdir -p /opt/vdlbot && sudo cp -r bot requirements.txt .env /opt/vdlbot/
sudo python3 -m venv /opt/vdlbot/.venv
sudo /opt/vdlbot/.venv/bin/pip install -r /opt/vdlbot/requirements.txt
sudo chown -R vdlbot:vdlbot /opt/vdlbot
sudo cp vdlbot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now vdlbot
journalctl -u vdlbot -f
```

## Настройки (.env)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `BOT_TOKEN` | — | Токен от @BotFather (обязательно) |
| `MAX_UPLOAD_MB` | `49` | Порог, под который ужимается видео |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Сколько загрузок идёт одновременно |
| `USER_COOLDOWN_SEC` | `5` | Пауза между действиями одного пользователя |
| `MAX_USER_QUEUE` | `2` | Максимум задач в очереди на пользователя |
| `DOWNLOAD_DIR` | `/tmp/vdlbot` | Временные файлы (чистятся после отправки) |
| `COOKIES_FILE` | — | Netscape-cookies для приватных/возрастных видео |
| `ALLOWED_USERS` | — | Белый список user id через запятую; пусто = все |
| `LOCAL_BOT_API` | — | Адрес своего Bot API server |
| `UNIVERSAL_MODE` | `1` | Принимать любые ссылки; `0` — только известные площадки |
| `DONATE_ENABLED` | `1` | Команда `/donate` и кнопки поддержки |
| `DONATE_EVERY` | `10` | Плашка о поддержке раз в N загрузок; `0` — никогда |
| `DB_PATH` | `/data/vdlbot.sqlite3` | База: счётчик загрузок и журнал донатов |

## Файлы больше 50 МБ

Обычный Bot API не даёт боту отправить файл тяжелее 50 МБ, поэтому по умолчанию видео
пережимается ffmpeg'ом (битрейт считается из длительности, при нехватке — понижается
разрешение). Если хочется отдавать оригиналы до 2 ГБ, поднимите свой Bot API server:

```bash
# в .env добавьте TELEGRAM_API_ID и TELEGRAM_API_HASH с https://my.telegram.org
docker compose -f docker-compose.yml -f docker-compose.localapi.yml up -d --build
```

Оверлей сам выставит боту `LOCAL_BOT_API` и `MAX_UPLOAD_MB=1900`.

## Cookies (Instagram, возрастные видео на YouTube)

Instagram почти всегда требует авторизации. Экспортируйте cookies расширением вида
«Get cookies.txt» в формате Netscape, положите файл рядом с проектом, в `.env` укажите
`COOKIES_FILE=/app/cookies.txt` и раскомментируйте монтирование в `docker-compose.yml`.
Используйте отдельный аккаунт: площадки могут блокировать аккаунт за автоматизацию.

## Площадки

По умолчанию `UNIVERSAL_MODE=1`: бот принимает ссылку с любого сайта и отдаёт её yt-dlp.
Если экстрактора нет — отвечает, что площадка не поддерживается. Известные площадки
(список `KNOWN_PLATFORMS` в `bot/downloader.py`) подписываются человеческим именем в
карточке, остальные — доменом.

В группах бот реагирует **только на известные видеоплощадки** — иначе он лез бы в каждую
ссылку в чате. В личке принимаются любые.

Чтобы ограничить бота тремя-пятью сайтами, поставьте `UNIVERSAL_MODE=0` и отредактируйте
`KNOWN_PLATFORMS`.

## Донаты

Бот бесплатный; поддержка добровольная и ни на что не влияет. Telegram разрешает
расчёты за цифровые услуги только в Telegram Stars, поэтому используется валюта `XTR`
и пустой `provider_token` — никакой Stripe или YooKassa подключать не нужно.

Как устроено:

- `/donate` показывает кнопки на 50 / 100 / 250 / 500 ⭐ (суммы — в `bot/donate.py`, `TIERS`)
- после каждой `DONATE_EVERY`-й загрузки приходит одна строчка с кнопкой «Поддержать»
- `pre_checkout_query` подтверждается автоматически (уложиться надо в 10 секунд)
- каждый платёж пишется в таблицу `donations` вместе с `telegram_payment_charge_id`

`charge_id` хранится не просто так: без него невозможен возврат через `refundStarPayment`.
Повторная доставка одного и того же платежа не удваивает сумму — `charge_id` первичный ключ.

Вывод собранных звёзд — через Fragment; условия и сроки удержания уточняйте в актуальной
документации Telegram, они менялись.

## Работа в группах

Добавьте бота в чат. Чтобы он видел сообщения, либо выдайте ему права администратора,
либо отключите privacy mode у @BotFather (`/setprivacy` → Disable). На сообщения без
ссылок бот в группах не отвечает. Нажать кнопку качества может только автор ссылки.

## Обновление yt-dlp

Площадки меняются часто — если что-то перестало скачиваться, первым делом обновите yt-dlp:

```bash
docker compose build --no-cache && docker compose up -d   # Docker
pip install -U yt-dlp                                     # venv
```

## Диагностика сетевых проблем

**`No address associated with hostname` (Errno -5)** — это DNS: сервер не смог превратить
имя площадки в IP. До самой площадки запрос не дошёл. Проверяем по шагам:

```bash
# 1. Резолвится ли имя внутри контейнера?
docker compose exec bot python -c "import socket; print(socket.getaddrinfo('www.instagram.com', 443)[0][4])"

# 2. А на самом хосте?
getent hosts www.instagram.com
```

- Ошибка только в контейнере, на хосте всё хорошо → проблема в DNS Docker.
  В `docker-compose.yml` уже прописаны `dns: 1.1.1.1 / 8.8.8.8`; примените
  `docker compose up -d` (перезапуска мало — нужно пересоздание контейнера).
- Ошибка и на хосте → имя не резолвит DNS провайдера/хостера. Пропишите публичные
  резолверы в `/etc/resolv.conf` (или в `/etc/docker/daemon.json` → `{"dns": ["1.1.1.1"]}`
  с `systemctl restart docker`). Если резолверы подменяются на уровне сети — нужен
  прокси (ниже) или сервер в другой сети.

**Прокси.** Если прямого доступа к площадке с сервера нет, укажите в `.env`:

```
PROXY=socks5://user:pass@127.0.0.1:1080
# или PROXY=http://10.0.0.5:3128
```

yt-dlp пойдёт через него. Telegram API при этом ходит напрямую — если и до него не
достучаться, поднимайте прокси на уровне системы. Для SOCKS5 с резолвом имён на стороне
прокси используйте схему `socks5h://`.

**`FORCE_IPV4=1`** — лечит случай, когда у сервера объявлен IPv6, но фактически не работает:
запросы висят и отваливаются по таймауту.

**`403 Forbidden` вместо DNS-ошибки** — это уже другое: имя отрезолвилось, но площадка
отклонила запрос. Тут помогают cookies или другой IP, а не настройки DNS.

## Структура

```
bot/
  main.py         точка входа, polling, middleware
  handlers.py     команды, приём ссылок, кнопки, отправка файла
  downloader.py   yt-dlp + ffmpeg: probe, скачивание, сжатие
  queue.py        очередь и воркер-пул
  middlewares.py  антиспам и белый список
  donate.py       донаты на Telegram Stars
  storage.py      SQLite: счётчик загрузок, журнал донатов
  config.py       конфиг из .env
  texts.py        тексты сообщений
```

## Ограничения

- Прямые эфиры не скачиваются, из плейлиста берётся только первый ролик.
- Очень длинные видео не получится ужать под 50 МБ без потери качества — бот предложит
  взять качество ниже или только аудио.
- Бот предназначен для личного использования; соблюдайте условия использования площадок
  и права авторов.
