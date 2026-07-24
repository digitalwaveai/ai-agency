# LeadPilot для Railway

Чистый Telegram-worker для постоянного запуска в Railway.

## Что работает

- меню и команды Telegram;
- поиск компаний через Google Maps API SerpAPI;
- сохранение лидов в PostgreSQL;
- просмотр последних лидов;
- подготовка персонализированного первого сообщения через OpenAI;
- проекты для разных ниш и регионов;
- много-радар по нескольким нишам и регионам;
- подробный анализ клиента;
- воронка и статусы лидов;
- аналитика и CSV-экспорт;
- лимиты, актуальные тарифы, настройки и поддержка;
- ограничение доступа по `OWNER_TELEGRAM_ID`;
- автоматический перезапуск процесса после сбоя.

Бот использует long polling. Отдельный публичный домен и HTTP-сервер ему не нужны.

## Переменные Railway

Добавьте в **Railway → сервис → Variables**:

```env
TELEGRAM_BOT_TOKEN=токен_от_BotFather
SERPAPI_KEY=приватный_ключ_SerpAPI
OPENAI_API_KEY=ключ_OpenAI_API
DATABASE_URL=${{Postgres.DATABASE_URL}}
OWNER_TELEGRAM_ID=ваш_числовой_Telegram_ID
DEMO_MODE=false
SEARCH_PROVIDER=serpapi
OPENAI_MODEL=gpt-5.6-luna
SUPPORT_TELEGRAM=@DigitalWave_vl
SUPPORT_EMAIL=ai.marketing.digital@mail.ru
```

Секретные значения нельзя добавлять в GitHub или отправлять в чат.

Если PostgreSQL в проекте Railway называется не `Postgres`, выберите переменную
`DATABASE_URL` через **Add Reference** и укажите реальное имя сервиса базы.

## Загрузка через браузер

1. Распакуйте ZIP.
2. Откройте репозиторий `digitalwaveai/ai-agency` на GitHub.
3. Нажмите **Add file → Upload files**.
4. Перетащите **содержимое** папки `leadpilot-railway-fix`, а не сам ZIP.
5. Нажмите **Commit changes**.
6. Откройте Railway и дождитесь нового deployment.
7. После статуса **Active** отправьте боту `/status`, затем `/start`.

Старые файлы репозитория можно не удалять: Railway запускает только
`python -m leadpilot`, заданный в `railway.json`.

## Локальная проверка

```bash
python -m unittest discover -s tests -v
python -m compileall -q leadpilot tests
```

Для локального запуска установите зависимости, задайте переменные окружения и
выполните:

```bash
python -m leadpilot
```

## Команды бота

- `/start` — меню;
- `/menu` — открыть полное меню;
- `/new_project` — создать проект;
- `/projects` — список проектов;
- `/find` — поиск клиентов;
- `/leads` — последние сохранённые лиды;
- `/message` — подготовить сообщение;
- `/analyze` — подробный анализ клиента;
- `/radars` — создать много-радар;
- `/radar_run ID` — повторно запустить сохранённый радар;
- `/analytics` — аналитика лидов;
- `/export` — выгрузить CSV;
- `/lead_status ID new|contacted|replied` — изменить этап воронки;
- `/plans` — тарифы;
- `/limits` — лимиты;
- `/support` — поддержка;
- `/status` — проверить работу;
- `/cancel` — отменить текущий шаг.
