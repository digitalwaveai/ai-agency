# Beauty Lead Finder Assistant

MVP-ассистент для поиска потенциальных клиентов в бьюти-нише. Проект аккуратно расширяет существующий статический лендинг и добавляет FastAPI backend, SQLite-базу, demo mode, Streamlit UI, scoring, deduplication, генерацию сообщений и CSV-экспорт.

## Возможности

- форма поиска по нише, географии, типу клиента, услугам, лимиту, score и контактам;
- генерация поисковых запросов под бьюти-нишу;
- поиск через demo mode без ключей или через адаптеры Brave Search / SerpAPI;
- сохранение лидов в SQLite;
- deduplication по домену, email, телефону, Instagram, Telegram, названию + городу;
- lead scoring 0–100 с объяснением;
- карточка лида, заметки и статусы: `new`, `qualified`, `contacted`, `replied`, `not_fit`, `do_not_contact`, `archived`;
- генерация 3 вариантов первого сообщения: мягкий, деловой, короткий;
- CSV-экспорт;
- заготовка регулярного обновления через APScheduler.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Настройка `.env`

```env
SEARCH_PROVIDER=demo
SERPAPI_KEY=
BRAVE_SEARCH_API_KEY=
BING_SEARCH_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
DATABASE_URL=sqlite:///./leads.db
REFRESH_INTERVAL_HOURS=24
DEMO_MODE=true
```

Для первого запуска оставьте `DEMO_MODE=true`: ассистент создаст демонстрационные лиды без внешних API.

## Запуск backend

```bash
uvicorn app.main:app --reload
```

Откройте:

- HTML MVP: <http://localhost:8000/>
- API docs: <http://localhost:8000/docs>

## Запуск UI

```bash
streamlit run app/ui/streamlit_app.py
```

## Поддерживаемые поисковые API

- `demo` — встроенные тестовые лиды;
- `brave` — Brave Search API, ключ `BRAVE_SEARCH_API_KEY`;
- `serpapi` — SerpAPI, ключ `SERPAPI_KEY`.

Bing и Google Custom Search предусмотрены переменными окружения и могут быть добавлены новым адаптером в `app/services/search_service.py`.

## Как добавить новый источник

1. Добавьте метод в `SearchService`, который возвращает список `SearchResult`.
2. Используйте только официальные API или публичные страницы, где это разрешено.
3. Не обходите CAPTCHA, авторизацию, paywall и ограничения сайта.
4. Соблюдайте robots.txt, rate limits и правила площадок.
5. Сохраняйте `source_url` для каждого факта.

## Как работает scoring

Плюсы:

- `+25` — совпадение с нишей;
- `+15` — есть контакт;
- `+15` — есть сайт или соцсеть;
- `+10` — признаки ручной записи: Direct, WhatsApp, «пишите в личку»;
- `+10` — нет нормального сайта или сайт слабый;
- `+10` — есть обучение, курс, консультации;
- `+10` — эксперт активен и коммерческий;
- `+5` — локальная привязка к городу.

Минусы:

- `-30` — не подтверждена бьюти-ниша;
- `-20` — крупная сеть или франшиза;
- `-20` — нет контактов;
- `-15` — уже есть сильная автоматизация;
- устаревшие лиды при refresh могут архивироваться, если score стал низким.

## Экспорт CSV

Через API:

```bash
curl -o leads.csv http://localhost:8000/export.csv
```

Через Streamlit — кнопка **Экспорт CSV**.

## Безопасное использование

Ассистент по умолчанию только готовит список и тексты сообщений. Он не отправляет рассылки автоматически. Используйте только публичные данные, официальные API и корректные лимиты. Если контакт не найден, проект пишет `не найден` и не выдумывает данные.

## Тесты

```bash
pytest
```
