# Simple Python Web Browser — Design Spec

Date: 2026-03-15

## Overview

A command-line Python web browser that fetches a URL using raw sockets, parses the HTTP response, and displays the plain text content of the page. No external packages required. Built for learning purposes.

## Usage

```bash
python3 browser.py <url>      # fetch a URL
python3 browser.py            # opens DEFAULT_URL (file:///etc/hosts)
```

Supported URL schemes: `http`, `https`, `file`, `data`, `view-source`

## Architecture

Single file: `browser.py`

```
parse_url(url)                      → (scheme, host, port, path)
_decode_chunked(data)               → bytes
request(url, max_redirect=10)       → (status_line, headers_dict, body_bytes)
TextExtractor(HTMLParser)           → visible text collector
show(body_bytes, headers_dict)      → prints plain text to stdout
main()                              → reads sys.argv[1], orchestrates the above
```

Module-level state:
- `_socket_pool` — keep-alive socket cache keyed by `(scheme, host, port)`
- `_cache` — HTTP response cache keyed by URL

---

## Function Specifications

### `parse_url(url) → (scheme, host, port, path)`

Handles special schemes before general parsing:

| Input prefix | Returns |
|---|---|
| `file://` | `("file", "", 0, /absolute/path)` |
| `data:` | `("data", "", 0, mime,content)` |
| `view-source:` | `("view-source", "", 0, inner_url)` |

For `http`/`https`:
- Splits on `://` to extract scheme
- Default ports: `http` → 80, `https` → 443
- Strips fragment (`#...`)
- Separates host from path on first `/`; path defaults to `/`
- Path includes query string (`?...`) if present
- Extracts explicit port from host (`host:8080`), converts to int

### `_decode_chunked(data: bytes) → bytes`

Decodes HTTP chunked transfer-encoding:
- Reads hex chunk size, then that many bytes of data
- Repeats until chunk size is `0`
- Returns concatenated raw body bytes

### `request(url, max_redirect=10) → (status_line, headers_dict, body_bytes)`

Dispatch order at entry:

1. **`file://`** — opens local file, returns `("200 OK", {"content-type": "text/html"}, bytes)`
2. **`data:`** — parses `mime,content`, returns body as UTF-8 bytes
3. **`view-source:`** — fetches inner URL recursively, sets `headers["_view_source"] = True`
4. **Cache lookup** — returns cached response if not expired
5. **HTTP/HTTPS network request**

Network request flow:
- Reuses socket from `_socket_pool` if available (keep-alive), otherwise creates new
- For HTTPS: wraps socket with `ssl.create_default_context().wrap_socket()`
- Request headers sent: `Host`, `Connection: keep-alive`, `User-Agent: SimpleBrowser/1.0`, `Accept-Encoding: gzip`
- Request line: `GET {path} HTTP/1.1`
- Reads response headers until `\r\n\r\n`, starting from any buffered excess bytes

Body reading:
| Condition | Behavior |
|---|---|
| `Content-Length` present | Read exactly N bytes; store excess in socket pool |
| `Transfer-Encoding: chunked` | Read until EOF, then decode with `_decode_chunked()` |
| Neither (no Content-Length) | Read with 3s timeout; treat `TimeoutError` as EOF |

Post-body processing (in order):
1. Chunked decoding if `Transfer-Encoding: chunked`
2. Gzip decompression if `Content-Encoding: gzip`
3. Cache store if status `200` and `Cache-Control` allows it
4. Redirect follow if status `3xx`

**Keep-alive socket pool:**
- Key: `(scheme, host, port)`
- Value: `(socket, excess_bytes)` — excess bytes from the previous response are replayed as the start of the next response's header
- Socket is NOT pooled when following a redirect

**Caching:**
- Only 200 responses are cached
- `Cache-Control: no-store` → never cache
- `Cache-Control: max-age=N` → cache for N seconds
- Any other Cache-Control value → do not cache
- Cache key is the full URL string

**Redirects:**
- Follows `Location` header on any 3xx response
- `//`-relative → prepend scheme
- `/`-relative → prepend `scheme://host[:port]`
- Raises `RuntimeError("Too many redirects")` after 10 hops

### `TextExtractor(HTMLParser)`

Subclass of `html.parser.HTMLParser` that collects visible text:
- Maintains `_skip` counter (int); increments on `<script>`/`<style>`, decrements on `</script>`/`</style>`
- Appends data to `_parts` only when `_skip == 0`
- `get_text()` calls `html.unescape()` on each part (decodes `&lt;`, `&gt;`, `&amp;`, etc.), then strips blank lines

### `show(body_bytes, headers_dict)`

- If `headers["_view_source"]` is `True`: decode as UTF-8 and print raw HTML, return
- Encoding resolution:
  1. Parse `charset=` from `Content-Type` header
  2. Try declared encoding, then `utf-8`, `euc-kr`, `latin-1` in order
- If `content-type` does not contain `html`: print raw decoded text
- Otherwise: feed through `TextExtractor`, print result

### `main()`

- URL = `sys.argv[1]` if provided, else `DEFAULT_URL`
- Prints four labeled sections separated by blank lines:
  1. `=== Request ===` — URL
  2. `=== Response Status ===` — raw status line
  3. `=== Response Headers ===` — all headers except internal `_`-prefixed keys
  4. `=== Body (text) ===` — output of `show()`

---

## Output Format

```
=== Request ===
URL: https://example.com

=== Response Status ===
HTTP/1.1 200 OK

=== Response Headers ===
content-type: text/html; charset=UTF-8
content-length: 1256

=== Body (text) ===
Example Domain
This domain is for use in illustrative examples...
```

---

## Error Handling

| Condition | Behavior |
|---|---|
| No URL argument | Opens `DEFAULT_URL` |
| Redirect loop > 10 | `RuntimeError: Too many redirects` |
| Socket timeout (headers) | Exception propagates |
| Socket timeout (body, no Content-Length) | Treat as EOF, return accumulated body |
| `UnicodeDecodeError` | Fallback chain: declared → UTF-8 → EUC-KR → latin-1 |
| Non-HTML content-type | Print raw decoded text |

---

## Constraints

- Standard library only: `socket`, `ssl`, `gzip`, `time`, `html`, `html.parser`, `sys`
- Python 3.6+
- HTTP/1.1 with keep-alive and gzip support
