# OpenShelf demo

Метапоиск легальных книг по IT / ИИ / ИБ без собственной базы.

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# http://127.0.0.1:8000
```

API: `GET /api/search?q=cryptography&lang=any|ru|en|uz&limit=10`

Источники: Internet Archive, Open Library (только public), DOAB, Google Books (free-ebooks).
Новый источник = одна async-функция `search(client, q, n) -> list[Book]` + запись в `ADAPTERS`.

Дальше: Redis вместо dict-кэша, rate limiting (slowapi), Telegram-бот поверх `/api/search`,
Unpaywall/Crossref для статей, NIST/OWASP как отдельные адаптеры, партнёрские ссылки «Купить».
