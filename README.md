# calibre-mcp

**Connect your Calibre ebook library to Claude AI via the Model Context Protocol.**

A lightweight MCP server that gives Claude (and any MCP-compatible AI) direct access to your Calibre library — metadata search, full-text content search, and library browsing. Runs as a Docker container on your NAS or any server.

## What it does

Ask Claude things like:
- *"Search my library for books about meditation"*
- *"Find passages mentioning the vagus nerve in any of my books"*
- *"List all books by Eckhart Tolle"*
- *"What tags do I have in my library?"*
- *"Show me stats about my ebook collection"*

Claude queries your Calibre database in real-time and returns results with full metadata.

## Tools exposed

| Tool | Description |
|------|-------------|
| `calibre_search_books` | Search by title, author, tag, series, or publisher |
| `calibre_search_content` | Full-text search inside book contents with snippets |
| `calibre_get_book` | Get complete metadata for a book by ID |
| `calibre_list_tags` | Browse all tags with book counts |
| `calibre_list_authors` | Browse all authors with book counts |
| `calibre_list_series` | Browse all series with book counts |
| `calibre_stats` | Library statistics overview |

## Prerequisites

- **Calibre 6+** with Full Text Search indexing enabled
- Your Calibre library folder must contain:
  - `metadata.db` (always present)
  - `full-text-search.db` (generated when you enable FTS in Calibre — click the "FT" button in the search bar)
- **Docker** on your NAS or server

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/calibre-mcp.git
cd calibre-mcp
```

Edit `docker-compose.yml` and set the path to your Calibre library:

```yaml
volumes:
  - /path/to/your/calibre/library:/calibre-library:ro
```

### 2. Build and run

```bash
docker-compose up -d --build
```

Verify it's running:

```bash
curl http://localhost:8100/mcp
# Should return a JSON-RPC error (expected — it needs an MCP client, not curl)
```

### 3. Expose to the internet

Claude needs to reach your server over HTTPS. Two free options:

**Option A: Tailscale Funnel (recommended)**

If you already use Tailscale:

```bash
tailscale funnel --bg 8100
```

This gives you a permanent HTTPS URL like `https://your-machine.tail12345.ts.net/`.

**Option B: Cloudflare Tunnel**

Requires a domain with DNS managed by Cloudflare. See [Cloudflare Tunnel docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

### 4. Connect to Claude

1. Go to [claude.ai](https://claude.ai) → Settings → Connectors
2. Click "Add Connector"
3. Enter your HTTPS URL + `/mcp` (e.g., `https://your-machine.tail12345.ts.net/mcp`)
4. Done — the connector is automatically available on Claude iOS/Android too

## Architecture

```
┌─────────────┐     HTTPS      ┌──────────────────┐
│  Claude AI   │ ◄────────────► │  Tailscale Funnel │
│ (web / iOS)  │                │  or CF Tunnel     │
└─────────────┘                └────────┬─────────┘
                                        │ localhost:8100
                               ┌────────▼─────────┐
                               │  calibre-mcp      │
                               │  (Docker)         │
                               └────────┬─────────┘
                                        │ read-only
                          ┌─────────────▼──────────────┐
                          │  Calibre Library            │
                          │  ├── metadata.db (13 MB)    │
                          │  └── full-text-search.db    │
                          │      (size varies, ~2MB/    │
                          │       1000 books)           │
                          └────────────────────────────┘
```

## How it works

- **metadata.db** is Calibre's main database containing all book metadata (titles, authors, tags, series, publishers, descriptions, formats, identifiers, ratings)
- **full-text-search.db** contains extracted text from all your ebooks, indexed by Calibre when FTS is enabled
- The MCP server opens both databases in **read-only mode** — it cannot modify your library
- Metadata searches are instant; full-text searches may take a few seconds on large libraries (uses SQL LIKE on the extracted text)
- When you add books through Calibre Desktop, they're automatically available to the MCP server

## Notes

- The server is **read-only** — it cannot modify, add, or delete anything in your library
- Full-text search uses SQL `LIKE` rather than Calibre's native FTS5 index (which requires a custom tokenizer not available in standard SQLite). This means searches work but aren't as fast as Calibre's built-in search on very large libraries
- The `full-text-search.db` can be large (several GB for thousands of books). The server queries it on disk without loading it into memory
- Always add books through **Calibre Desktop** (not by copying files directly) to keep the databases in sync

## Security

- The server has no built-in authentication. Security relies on your tunnel (Tailscale Funnel restricts access, Cloudflare Access can add auth)
- All database access is read-only
- No personal data is transmitted — Claude sees only what you search for and the matching results

## License

MIT
