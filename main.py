"""Демо: метапоиск легальных книг по IT / ИИ / ИБ без собственной базы.

Запрос -> параллельный опрос внешних API -> нормализация -> дедупликация -> прямые ссылки.
Файлы через наш сервер не проходят: пользователь скачивает с источника.
"""
import asyncio
import re
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

TIMEOUT = 4.0        # секунд на каждый источник
CACHE_TTL = 3600     # секунд; в проде замените на Redis
CACHE_MAX = 500
HEADERS = {"User-Agent": "OpenShelfDemo/0.1 (replace-with-your-contact@example.com)"}

LANGS = {"russian": "ru", "rus": "ru", "ru": "ru", "english": "en", "eng": "en", "en": "en",
         "uzbek": "uz", "uzb": "uz", "uz": "uz"}


def norm_lang(v) -> str:
    v = str(v).strip().lower()
    return LANGS.get(v, v[:2])


def cc_label(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"creativecommons\.org/(licenses|publicdomain)/([\w-]+)/?([\d.]*)", url)
    if not m:
        return "см. источник"
    kind, code, ver = m.groups()
    if kind == "publicdomain":
        return "CC0" if code == "zero" else "Public domain"
    return f"CC {code.upper()} {ver}".strip()


def as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def to_year(v) -> int | None:
    m = re.search(r"\d{4}", str(v or ""))
    return int(m.group()) if m else None


@dataclass
class Book:
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    languages: list[str] = field(default_factory=list)
    license: str | None = None
    isbn: str | None = None
    links: list[dict] = field(default_factory=list)  # {source, url, kind: read|download}


# ---------------------------------------------------------------- адаптеры
async def ia_search(c: httpx.AsyncClient, q: str, n: int) -> list[Book]:
    """Internet Archive: только неограниченный доступ."""
    r = await c.get("https://archive.org/advancedsearch.php", params={
        "q": f"({q}) AND mediatype:texts AND -access-restricted-item:true",
        "fl[]": ["identifier", "title", "creator", "year", "language", "licenseurl"],
        "sort[]": "downloads desc", "rows": n, "output": "json"})
    r.raise_for_status()
    out = []
    for d in r.json().get("response", {}).get("docs", []):
        i = d["identifier"]
        out.append(Book(
            title=str(as_list(d.get("title"))[0] if d.get("title") else i),
            authors=[str(a) for a in as_list(d.get("creator"))],
            year=to_year(d.get("year")),
            languages=[norm_lang(x) for x in as_list(d.get("language"))],
            license=cc_label(d.get("licenseurl")) or "свободный доступ",
            links=[{"source": "Internet Archive", "kind": "download", "url": f"https://archive.org/download/{i}"},
                   {"source": "Internet Archive", "kind": "read", "url": f"https://archive.org/details/{i}"}]))
    return out


async def ol_search(c: httpx.AsyncClient, q: str, n: int) -> list[Book]:
    """Open Library: только полностью открытые издания (ebook_access=public)."""
    r = await c.get("https://openlibrary.org/search.json", params={
        "q": q, "has_fulltext": "true", "limit": n,
        "fields": "key,title,author_name,first_publish_year,isbn,ia,language,ebook_access"})
    r.raise_for_status()
    out = []
    for d in r.json().get("docs", []):
        if d.get("ebook_access") != "public":
            continue
        links = [{"source": "Open Library", "kind": "read", "url": "https://openlibrary.org" + d["key"]}]
        if d.get("ia"):
            links.insert(0, {"source": "Open Library", "kind": "download",
                             "url": f"https://archive.org/download/{d['ia'][0]}"})
        out.append(Book(
            title=d.get("title", "—"), authors=d.get("author_name", [])[:3],
            year=d.get("first_publish_year"),
            languages=[norm_lang(x) for x in d.get("language", [])],
            license="свободный доступ", isbn=(d.get("isbn") or [None])[0], links=links))
    return out


async def doab_search(c: httpx.AsyncClient, q: str, n: int) -> list[Book]:
    """DOAB: рецензируемые книги open access (Springer, MIT Press, Cambridge и др.)."""
    r = await c.get("https://directory.doabooks.org/rest/search",
                    params={"query": q, "expand": "metadata", "limit": n})
    r.raise_for_status()

    def md(item, key):
        return [m["value"] for m in item.get("metadata", []) if m.get("key") == key]

    out = []
    for it in r.json():
        titles = md(it, "dc.title")
        if not titles:
            continue
        uri = next((u for u in md(it, "dc.identifier.uri") if u.startswith("http")),
                   f"https://directory.doabooks.org/handle/{it.get('handle', '')}")
        out.append(Book(
            title=titles[0], authors=md(it, "dc.contributor.author")[:3],
            year=to_year((md(it, "dc.date.issued") or [None])[0]),
            languages=[norm_lang(x) for x in md(it, "dc.language")],
            license=cc_label((md(it, "dc.rights.uri") or [None])[0]) or "open access",
            links=[{"source": "DOAB", "kind": "read", "url": uri}]))
    return out


async def gb_search(c: httpx.AsyncClient, q: str, n: int) -> list[Book]:
    """Google Books: только free-ebooks. Без ключа квота небольшая."""
    r = await c.get("https://www.googleapis.com/books/v1/volumes", params={
        "q": q, "filter": "free-ebooks", "printType": "books", "maxResults": min(n, 40)})
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        v, a = it.get("volumeInfo", {}), it.get("accessInfo", {})
        links = []
        for fmt in ("pdf", "epub"):
            if a.get(fmt, {}).get("downloadLink"):
                links.append({"source": "Google Books", "kind": "download", "url": a[fmt]["downloadLink"]})
        links.append({"source": "Google Books", "kind": "read", "url": v.get("infoLink", "")})
        isbn = next((i["identifier"] for i in v.get("industryIdentifiers", []) if i.get("type") == "ISBN_13"), None)
        out.append(Book(
            title=v.get("title", "—"), authors=v.get("authors", [])[:3],
            year=to_year(v.get("publishedDate")),
            languages=[norm_lang(v["language"])] if v.get("language") else [],
            license="free ebook", isbn=isbn, links=[l for l in links if l["url"]]))
    return out


ADAPTERS = {"Internet Archive": ia_search, "Open Library": ol_search,
            "DOAB": doab_search, "Google Books": gb_search}


# ---------------------------------------------------------------- ядро
def dedupe_key(b: Book) -> str:
    t = re.sub(r"[\W_]+", "", b.title.lower())[:60]
    a = re.sub(r"\W+", "", b.authors[0].split()[-1].lower()) if b.authors else ""
    return f"{t}|{a}"


def merge(books: list[Book]) -> list[Book]:
    seen: dict[str, Book] = {}
    for b in books:
        k = dedupe_key(b)
        if k not in seen:
            seen[k] = b
            continue
        m = seen[k]
        m.links += [l for l in b.links if l["url"] not in {x["url"] for x in m.links}]
        m.year = m.year or b.year
        m.isbn = m.isbn or b.isbn
        m.authors = m.authors or b.authors
        m.languages = m.languages or b.languages
    return list(seen.values())


def score(b: Book) -> tuple:
    has_dl = any(l["kind"] == "download" for l in b.links)
    return (-len({l["source"] for l in b.links}), not has_dl)


async def run_source(name, fn, c, q, n):
    t = time.perf_counter()
    try:
        books = await asyncio.wait_for(fn(c, q, n), TIMEOUT)
        return name, books, {"ok": True, "count": len(books), "ms": int((time.perf_counter() - t) * 1000)}
    except Exception as e:  # один упавший источник не ломает остальные
        return name, [], {"ok": False, "count": 0, "error": type(e).__name__}


async def federated_search(c: httpx.AsyncClient, q: str, lang: str, n: int) -> dict:
    t = time.perf_counter()
    res = await asyncio.gather(*(run_source(k, f, c, q, n) for k, f in ADAPTERS.items()))
    books = merge([b for _, bs, _ in res for b in bs])
    if lang != "any":
        books = [b for b in books if not b.languages or lang in b.languages]
    books.sort(key=score)
    return {"query": q, "took_ms": int((time.perf_counter() - t) * 1000),
            "sources": {name: st for name, _, st in res},
            "results": [asdict(b) for b in books]}


# ---------------------------------------------------------------- API
cache: dict[tuple, tuple[float, dict]] = {}
BASE = Path(__file__).parent
STATIC = BASE / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
    yield
    await app.state.http.aclose()


app = FastAPI(title="OpenShelf demo", lifespan=lifespan)


@app.get("/api/search")
async def search(q: str = Query(..., min_length=2, max_length=100),
                 lang: str = Query("any", pattern="^(any|ru|en|uz)$"),
                 limit: int = Query(10, ge=1, le=25)):
    q = re.sub(r"[^\w\s\-+.#]", " ", q, flags=re.U).strip()   # убираем синтаксис Lucene
    if len(q) < 2:
        return {"query": q, "took_ms": 0, "sources": {}, "results": []}
    key = (q.lower(), lang, limit)
    hit = cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    data = await federated_search(app.state.http, q, lang, limit)
    if any(s["ok"] for s in data["sources"].values()):        # не кэшируем полный отказ
        if len(cache) >= CACHE_MAX:
            cache.pop(next(iter(cache)))
        cache[key] = (time.time(), data)
    return data


@app.get("/")
async def index():
    for p in (STATIC / "index.html", BASE / "index.html"):    # в static/ или рядом с main.py
        if p.exists():
            return FileResponse(p)
    return {"error": "index.html not found next to main.py or in static/"}
