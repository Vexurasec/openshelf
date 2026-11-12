# OpenShelf

**Live demo:** https://openshelf-ccyl.onrender.com/

Метапоиск книг в открытом доступе без собственной базы. Один запрос опрашивает Internet Archive, Open Library, DOAB и Google Books и отдаёт прямые ссылки на источники.

## Запуск

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# http://127.0.0.1:8000
```

## API

`GET /api/search?q=cryptography&limit=10`

## Источники

Internet Archive, Open Library (только public), DOAB, Google Books (free-ebooks).
Новый источник: одна async-функция `search(client, q, n) -> list[Book]` и запись в `ADAPTERS`.

## Дальше

Redis вместо dict-кэша, rate limiting, Telegram-бот поверх `/api/search`, адаптеры NIST/OWASP/arXiv, партнёрские ссылки «Купить».

Made by [Vexura](https://github.com/Vexurasec)
