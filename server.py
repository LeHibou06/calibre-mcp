"""
Calibre MCP Server
Connect your Calibre ebook library to Claude AI via the Model Context Protocol.
Exposes metadata search, full-text content search, and library browsing tools.

Reads metadata.db and full-text-search.db (SQLite) in read-only mode.
Requires Calibre 6+ with Full Text Search indexing enabled.
"""

import sqlite3
import os
import re
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

# ---------------------------------------------------------------------------
# Configuration — override via environment variables
# ---------------------------------------------------------------------------

CALIBRE_LIBRARY = os.environ.get("CALIBRE_LIBRARY_PATH", "/calibre-library")
METADATA_DB = os.path.join(CALIBRE_LIBRARY, "metadata.db")
FTS_DB = os.path.join(CALIBRE_LIBRARY, "full-text-search.db")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("calibre_mcp")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def get_db():
    """Open metadata.db read-only and attach full-text-search.db."""
    conn = sqlite3.connect(f"file:{METADATA_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    conn.execute(f'ATTACH DATABASE "file:{FTS_DB}?mode=ro" AS fts_db;')
    return conn


def build_book_dict(row, conn):
    """Enrich a raw book row with authors, tags, series, formats, etc."""
    book = dict(row)
    bid = book["id"]

    authors = conn.execute(
        "SELECT a.name FROM authors a "
        "JOIN books_authors_link bal ON a.id = bal.author WHERE bal.book = ?",
        (bid,),
    ).fetchall()
    book["authors"] = [a["name"] for a in authors]

    tags = conn.execute(
        "SELECT t.name FROM tags t "
        "JOIN books_tags_link btl ON t.id = btl.tag WHERE btl.book = ?",
        (bid,),
    ).fetchall()
    book["tags"] = [t["name"] for t in tags]

    series = conn.execute(
        "SELECT s.name, b.series_index FROM series s "
        "JOIN books_series_link bsl ON s.id = bsl.series "
        "JOIN books b ON b.id = bsl.book WHERE bsl.book = ?",
        (bid,),
    ).fetchone()
    book["series"] = series["name"] if series else None
    book["series_index"] = series["series_index"] if series else None

    pub = conn.execute(
        "SELECT p.name FROM publishers p "
        "JOIN books_publishers_link bpl ON p.id = bpl.publisher WHERE bpl.book = ?",
        (bid,),
    ).fetchone()
    book["publisher"] = pub["name"] if pub else None

    langs = conn.execute(
        "SELECT l.lang_code FROM languages l "
        "JOIN books_languages_link bll ON l.id = bll.lang_code WHERE bll.book = ?",
        (bid,),
    ).fetchall()
    book["languages"] = [la["lang_code"] for la in langs]

    formats = conn.execute(
        "SELECT format, uncompressed_size FROM data WHERE book = ?", (bid,)
    ).fetchall()
    book["formats"] = [f["format"] for f in formats]

    comment = conn.execute(
        "SELECT text FROM comments WHERE book = ?", (bid,)
    ).fetchone()
    book["description"] = comment["text"] if comment else None

    idents = conn.execute(
        "SELECT type, val FROM identifiers WHERE book = ?", (bid,)
    ).fetchall()
    book["identifiers"] = {i["type"]: i["val"] for i in idents}

    rating = conn.execute(
        "SELECT r.rating FROM ratings r "
        "JOIN books_ratings_link brl ON r.id = brl.rating WHERE brl.book = ?",
        (bid,),
    ).fetchone()
    book["rating"] = rating["rating"] if rating else None

    for key in ("sort", "author_sort", "lccn", "flags", "path"):
        book.pop(key, None)

    return book


def format_book_markdown(book):
    """Format a single book as readable Markdown."""
    lines = [f"### {book['title']}"]
    if book.get("authors"):
        lines.append(f"**Authors**: {', '.join(book['authors'])}")
    if book.get("series"):
        lines.append(f"**Series**: {book['series']} #{book.get('series_index', '')}")
    if book.get("publisher"):
        lines.append(f"**Publisher**: {book['publisher']}")
    if book.get("pubdate"):
        lines.append(f"**Date**: {book['pubdate'][:10]}")
    if book.get("languages"):
        lines.append(f"**Languages**: {', '.join(book['languages'])}")
    if book.get("tags"):
        lines.append(f"**Tags**: {', '.join(book['tags'])}")
    if book.get("formats"):
        lines.append(f"**Formats**: {', '.join(book['formats'])}")
    if book.get("rating"):
        stars = book["rating"] // 2
        lines.append(f"**Rating**: {'★' * stars}{'☆' * (5 - stars)}")
    if book.get("identifiers"):
        ids = ", ".join(f"{k}: {v}" for k, v in book["identifiers"].items())
        lines.append(f"**Identifiers**: {ids}")
    if book.get("description"):
        desc = re.sub(r"<[^>]+>", "", book["description"])
        if len(desc) > 500:
            desc = desc[:500] + "..."
        lines.append(f"\n{desc}")
    lines.append(f"\n*Calibre ID: {book['id']}*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Server initialization
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "calibre_mcp",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_PORT", "8100")),
    transport_security={
        "enable_dns_rebinding_protection": False,
        "allowed_hosts": ["*"],
        "allowed_origins": ["*"],
    },
)

# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class SearchBooksInput(BaseModel):
    """Search books by metadata fields."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., description="Search term for title, author, tag, series, or publisher.", min_length=1, max_length=200)
    field: Optional[str] = Field(default=None, description="Restrict to: 'title', 'author', 'tag', 'series', 'publisher'.")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SearchContentInput(BaseModel):
    """Full-text search inside book contents."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., description="Words or phrase to search inside books.", min_length=1, max_length=300)
    use_stemming: bool = Field(default=True, description="Reserved for future use.")
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class GetBookInput(BaseModel):
    """Get book by Calibre ID."""
    model_config = ConfigDict(extra="forbid")
    book_id: int = Field(..., description="Calibre book ID", ge=1)


class ListInput(BaseModel):
    """List with optional filter and pagination."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    filter: Optional[str] = Field(default=None, description="Substring filter (case-insensitive)")
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(name="calibre_search_books", annotations={"title": "Search books by metadata", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def calibre_search_books(params: SearchBooksInput) -> str:
    """Search the Calibre library by title, author, tag, series, or publisher."""
    conn = get_db()
    try:
        q = f"%{params.query}%"
        field = params.field
        if field == "title":
            where, sql_params = "b.title LIKE ?", [q]
        elif field == "author":
            where, sql_params = "b.id IN (SELECT bal.book FROM books_authors_link bal JOIN authors a ON a.id = bal.author WHERE a.name LIKE ?)", [q]
        elif field == "tag":
            where, sql_params = "b.id IN (SELECT btl.book FROM books_tags_link btl JOIN tags t ON t.id = btl.tag WHERE t.name LIKE ?)", [q]
        elif field == "series":
            where, sql_params = "b.id IN (SELECT bsl.book FROM books_series_link bsl JOIN series s ON s.id = bsl.series WHERE s.name LIKE ?)", [q]
        elif field == "publisher":
            where, sql_params = "b.id IN (SELECT bpl.book FROM books_publishers_link bpl JOIN publishers p ON p.id = bpl.publisher WHERE p.name LIKE ?)", [q]
        else:
            where = ("(b.title LIKE ? OR b.id IN (SELECT bal.book FROM books_authors_link bal "
                     "JOIN authors a ON a.id = bal.author WHERE a.name LIKE ?) OR b.id IN "
                     "(SELECT btl.book FROM books_tags_link btl JOIN tags t ON t.id = btl.tag "
                     "WHERE t.name LIKE ?) OR b.id IN (SELECT bsl.book FROM books_series_link bsl "
                     "JOIN series s ON s.id = bsl.series WHERE s.name LIKE ?) OR b.id IN "
                     "(SELECT bpl.book FROM books_publishers_link bpl JOIN publishers p ON "
                     "p.id = bpl.publisher WHERE p.name LIKE ?))")
            sql_params = [q] * 5

        total = conn.execute(f"SELECT COUNT(*) as total FROM books b WHERE {where}", sql_params).fetchone()["total"]
        sql_params.extend([params.limit, params.offset])
        rows = conn.execute(f"SELECT b.* FROM books b WHERE {where} ORDER BY b.last_modified DESC LIMIT ? OFFSET ?", sql_params).fetchall()

        if not rows:
            return f"No books found for '{params.query}'."

        books = [build_book_dict(r, conn) for r in rows]
        parts = [f"## Results for '{params.query}' ({total} books)\n"]
        for book in books:
            parts.append(format_book_markdown(book))
            parts.append("---")

        if total > params.offset + len(rows):
            parts.append(f"\n*Page {params.offset // params.limit + 1} — {len(rows)} shown of {total}. Use offset={params.offset + params.limit} for next page.*")
        return "\n\n".join(parts)
    finally:
        conn.close()


@mcp.tool(name="calibre_search_content", annotations={"title": "Full-text search in book contents", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def calibre_search_content(params: SearchContentInput) -> str:
    """Search the full text of all indexed books. Returns snippets around matches."""
    conn = get_db()
    try:
        search_term = f"%{params.query}%"
        rows = conn.execute(
            "SELECT bt.book, bt.format, bt.searchable_text "
            "FROM fts_db.books_text bt WHERE bt.searchable_text LIKE ? "
            "GROUP BY bt.book LIMIT ? OFFSET ?",
            (search_term, params.limit, params.offset),
        ).fetchall()

        if not rows:
            return f"No results for '{params.query}' in book contents."

        parts = [f"## Full-text search: '{params.query}'\n"]
        for row in rows:
            book_id, fmt, text = row["book"], row["format"], row["searchable_text"]
            pos = text.lower().find(params.query.lower())
            if pos >= 0:
                start, end = max(0, pos - 150), min(len(text), pos + len(params.query) + 150)
                snippet = text[start:end].replace("\n", " ")
                snippet = ("..." if start > 0 else "") + snippet + ("..." if end < len(text) else "")
            else:
                snippet = text[:300].replace("\n", " ") + "..."

            book_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
            if not book_row:
                continue
            book = build_book_dict(book_row, conn)
            parts.append(f"### {book['title']}")
            parts.append(f"**{', '.join(book.get('authors', []))}** — indexed format: {fmt}")
            if book.get("tags"):
                parts.append(f"Tags: {', '.join(book['tags'])}")
            parts.append(f"\n> {snippet}\n")
            parts.append(f"*Calibre ID: {book_id}*\n---")
        return "\n\n".join(parts)
    finally:
        conn.close()


@mcp.tool(name="calibre_get_book", annotations={"title": "Get book details by ID", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def calibre_get_book(params: GetBookInput) -> str:
    """Get complete metadata for a specific book by its Calibre ID."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (params.book_id,)).fetchone()
        if not row:
            return f"No book found with ID {params.book_id}."
        return format_book_markdown(build_book_dict(row, conn))
    finally:
        conn.close()


@mcp.tool(name="calibre_list_tags", annotations={"title": "List all tags", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def calibre_list_tags(params: ListInput) -> str:
    """List all tags in the library with book counts."""
    conn = get_db()
    try:
        where, sql_params = "", []
        if params.filter:
            where, sql_params = "WHERE t.name LIKE ?", [f"%{params.filter}%"]
        total = conn.execute(f"SELECT COUNT(*) as c FROM tags t {where}", sql_params).fetchone()["c"]
        sql_params.extend([params.limit, params.offset])
        rows = conn.execute(f"SELECT t.name, COUNT(btl.book) as book_count FROM tags t LEFT JOIN books_tags_link btl ON t.id = btl.tag {where} GROUP BY t.id ORDER BY book_count DESC LIMIT ? OFFSET ?", sql_params).fetchall()
        parts = [f"## Tags ({total} total)\n"] + [f"- **{r['name']}** ({r['book_count']} books)" for r in rows]
        if total > params.offset + len(rows):
            parts.append(f"\n*{len(rows)} shown of {total}. Use offset={params.offset + params.limit} for more.*")
        return "\n".join(parts)
    finally:
        conn.close()


@mcp.tool(name="calibre_list_authors", annotations={"title": "List all authors", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def calibre_list_authors(params: ListInput) -> str:
    """List all authors in the library with book counts."""
    conn = get_db()
    try:
        where, sql_params = "", []
        if params.filter:
            where, sql_params = "WHERE a.name LIKE ?", [f"%{params.filter}%"]
        total = conn.execute(f"SELECT COUNT(*) as c FROM authors a {where}", sql_params).fetchone()["c"]
        sql_params.extend([params.limit, params.offset])
        rows = conn.execute(f"SELECT a.name, COUNT(bal.book) as book_count FROM authors a LEFT JOIN books_authors_link bal ON a.id = bal.author {where} GROUP BY a.id ORDER BY book_count DESC LIMIT ? OFFSET ?", sql_params).fetchall()
        parts = [f"## Authors ({total} total)\n"] + [f"- **{r['name']}** ({r['book_count']} books)" for r in rows]
        if total > params.offset + len(rows):
            parts.append(f"\n*{len(rows)} shown of {total}. Use offset={params.offset + params.limit} for more.*")
        return "\n".join(parts)
    finally:
        conn.close()


@mcp.tool(name="calibre_list_series", annotations={"title": "List all series", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def calibre_list_series(params: ListInput) -> str:
    """List all book series in the library with book counts."""
    conn = get_db()
    try:
        where, sql_params = "", []
        if params.filter:
            where, sql_params = "WHERE s.name LIKE ?", [f"%{params.filter}%"]
        total = conn.execute(f"SELECT COUNT(*) as c FROM series s {where}", sql_params).fetchone()["c"]
        sql_params.extend([params.limit, params.offset])
        rows = conn.execute(f"SELECT s.name, COUNT(bsl.book) as book_count FROM series s LEFT JOIN books_series_link bsl ON s.id = bsl.series {where} GROUP BY s.id ORDER BY book_count DESC LIMIT ? OFFSET ?", sql_params).fetchall()
        parts = [f"## Series ({total} total)\n"] + [f"- **{r['name']}** ({r['book_count']} books)" for r in rows]
        if total > params.offset + len(rows):
            parts.append(f"\n*{len(rows)} shown of {total}. Use offset={params.offset + params.limit} for more.*")
        return "\n".join(parts)
    finally:
        conn.close()


@mcp.tool(name="calibre_stats", annotations={"title": "Library statistics", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def calibre_stats() -> str:
    """Get overall statistics about the Calibre library."""
    conn = get_db()
    try:
        books = conn.execute("SELECT COUNT(*) as c FROM books").fetchone()["c"]
        authors = conn.execute("SELECT COUNT(*) as c FROM authors").fetchone()["c"]
        tags = conn.execute("SELECT COUNT(*) as c FROM tags").fetchone()["c"]
        series_count = conn.execute("SELECT COUNT(*) as c FROM series").fetchone()["c"]
        publishers = conn.execute("SELECT COUNT(*) as c FROM publishers").fetchone()["c"]
        formats = conn.execute("SELECT format, COUNT(*) as c FROM data GROUP BY format ORDER BY c DESC").fetchall()
        fmt_lines = ", ".join(f"{f['format']}: {f['c']}" for f in formats)
        fts_books = conn.execute("SELECT COUNT(DISTINCT book) as c FROM fts_db.books_text").fetchone()["c"]
        fts_total = conn.execute("SELECT COUNT(*) as c FROM fts_db.books_text").fetchone()["c"]
        langs = conn.execute("SELECT l.lang_code, COUNT(bll.book) as c FROM languages l JOIN books_languages_link bll ON l.id = bll.lang_code GROUP BY l.lang_code ORDER BY c DESC LIMIT 10").fetchall()
        lang_lines = ", ".join(f"{la['lang_code']}: {la['c']}" for la in langs)
        return f"""## Library Statistics

**Books**: {books}
**Authors**: {authors}
**Tags**: {tags}
**Series**: {series_count}
**Publishers**: {publishers}

**Formats**: {fmt_lines}
**Languages**: {lang_lines}

**Full-text index**: {fts_books} books indexed ({fts_total} entries incl. multiple formats)"""
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
