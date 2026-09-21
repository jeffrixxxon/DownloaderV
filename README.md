# vdlbot — телеграм-бот для скачивания видео

Принимает ссылку на **YouTube**, **Instagram** или **Coub**, предлагает качество кнопками
и присылает готовый файл. Под капотом — [yt-dlp](https://github.com/yt-dlp/yt-dlp) и ffmpeg.

## Что умеет

- Выбор качества: 1080p / 720p / 480p / 360p и 🎵 только аудио (mp3 192 kbps)
- Автоматическое сжатие ffmpeg'ом, если файл не влезает в лимит Telegram (по умолчанию 49 МБ)
- Работа в группах: реагирует только на сообщения со ссылками, отвечает реплаем
- Очередь загрузок с ограничением параллелизма и антиспам-кулдаун на пользователя
- Опциональный белый список пользователей (`ALLOWED_USERS`)
- Опциональный локальный Bot API server — файлы до 2 ГБ вместо 50 МБ

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

## Структура

```
bot/
  main.py         точка входа, polling, middleware
  handlers.py     команды, приём ссылок, кнопки, отправка файла
  downloader.py   yt-dlp + ffmpeg: probe, скачивание, сжатие
  queue.py        очередь и воркер-пул
  middlewares.py  антиспам и белый список
  config.py       конфиг из .env
  texts.py        тексты сообщений
```

## Ограничения

- Прямые эфиры не скачиваются, из плейлиста берётся только первый ролик.
- Очень длинные видео не получится ужать под 50 МБ без потери качества — бот предложит
  взять качество ниже или только аудио.
- Бот предназначен для личного использования; соблюдайте условия использования площадок
  и права авторов.
