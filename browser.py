import socket
import ssl
import sys
import gzip
import time
import html as html_module
from html.parser import HTMLParser


DEFAULT_URL = "file:///etc/hosts"

_socket_pool: dict = {}  # (scheme, host, port) -> (socket, excess_bytes)
_cache: dict = {}        # url -> (expires_at, status_line, headers, body)


def parse_url(url):
    """Return (scheme, host, port, path) from a URL string."""
    if url.startswith("file://"):
        return "file", "", 0, url[len("file://"):]
    if url.startswith("data:"):
        return "data", "", 0, url[len("data:"):]
    if url.startswith("view-source:"):
        return "view-source", "", 0, url[len("view-source:"):]

    scheme, rest = url.split("://", 1)
    # Strip fragment
    if "#" in rest:
        rest = rest[:rest.index("#")]
    # Separate host+port from path
    if "/" in rest:
        host_port, path = rest.split("/", 1)
        path = "/" + path
    else:
        host_port = rest
        path = "/"
    # Extract port from host if present
    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"Invalid port in URL: {port_str!r}")
    else:
        host = host_port
        port = 443 if scheme == "https" else 80
    return scheme, host, port, path


def _decode_chunked(data: bytes) -> bytes:
    """Decode chunked transfer-encoding data."""
    result = b""
    while data:
        newline = data.index(b"\r\n")
        size = int(data[:newline], 16)
        if size == 0:
            break
        result += data[newline + 2: newline + 2 + size]
        data = data[newline + 2 + size + 2:]
    return result


def request(url, max_redirect=10):
    """Fetch URL, return (status_line, headers_dict, body_bytes).
    Follows 3xx redirects up to max_redirect times.
    Supports: http, https, file, data, view-source schemes.
    Reuses keep-alive sockets and caches 200 responses per Cache-Control.
    """
    scheme, host, port, path = parse_url(url)

    # file:// scheme
    if scheme == "file":
        with open(path, "rb") as f:
            body = f.read()
        return "200 OK", {"content-type": "text/html"}, body

    # data: scheme
    if scheme == "data":
        meta, _, content = path.partition(",")
        content_type = meta if meta else "text/plain"
        return "200 OK", {"content-type": content_type}, content.encode("utf-8")

    # view-source: scheme
    if scheme == "view-source":
        inner_status, inner_headers, inner_body = request(path, max_redirect)
        inner_headers["_view_source"] = True
        return "200 OK", inner_headers, inner_body

    # Cache lookup
    if url in _cache:
        expires_at, cached_status, cached_headers, cached_body = _cache[url]
        if time.time() < expires_at:
            return cached_status, cached_headers, cached_body
        else:
            del _cache[url]

    # Socket: reuse from pool or create new
    pool_key = (scheme, host, port)
    entry = _socket_pool.pop(pool_key, None)
    if entry is not None:
        s, init_buffer = entry
    else:
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(10)
        if scheme == "https":
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(raw_sock, server_hostname=host)
        else:
            s = raw_sock
        s.connect((host, port))
        init_buffer = b""

    headers_to_send = {
        "Host": host,
        "Connection": "keep-alive",
        "User-Agent": "SimpleBrowser/1.0",
        "Accept-Encoding": "gzip",
    }
    header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers_to_send.items())
    req = f"GET {path} HTTP/1.1\r\n{header_lines}\r\n\r\n"
    s.sendall(req.encode("utf-8"))

    # Read response headers (start with any excess bytes from previous response)
    raw_header = init_buffer
    while b"\r\n\r\n" not in raw_header:
        chunk = s.recv(4096)
        if not chunk:
            break
        raw_header += chunk
    header_section, _, leftover = raw_header.partition(b"\r\n\r\n")
    header_lines_list = header_section.decode("utf-8", errors="replace").split("\r\n")
    status_line = header_lines_list[0]
    headers = {}
    for line in header_lines_list[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v

    # Determine redirect early so we know whether to pool the socket
    status_code = status_line.split(" ", 2)[1] if " " in status_line else ""
    is_redirect = status_code.startswith("3") and "location" in headers

    # Read body
    content_length = int(headers.get("content-length", -1))
    body = leftover
    if content_length >= 0:
        while len(body) < content_length:
            chunk = s.recv(4096)
            if not chunk:
                break
            body += chunk
        excess = body[content_length:]  # bytes belonging to the next response
        body = body[:content_length]
        if not is_redirect:
            _socket_pool[pool_key] = (s, excess)  # return socket to pool
    else:
        # No Content-Length: read until server closes connection.
        # HTTP/1.1 keep-alive servers won't send EOF, so treat a timeout
        # as end-of-body and close the socket.
        s.settimeout(3)
        while True:
            try:
                chunk = s.recv(4096)
            except (TimeoutError, OSError):
                break  # server kept connection alive — treat as EOF
            if not chunk:
                break
            body += chunk
        s.close()

    # Chunked decoding
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _decode_chunked(body)
        _socket_pool.pop(pool_key, None)  # cannot safely reuse after chunked

    # Gzip decompression
    if headers.get("content-encoding", "").lower() == "gzip":
        body = gzip.decompress(body)

    # Cache 200 responses per Cache-Control
    if status_code == "200":
        cache_control = headers.get("cache-control", "")
        if "no-store" not in cache_control:
            max_age = None
            for part in cache_control.split(","):
                part = part.strip()
                if part.startswith("max-age="):
                    try:
                        max_age = int(part[len("max-age="):])
                    except ValueError:
                        pass
            if max_age is not None:
                _cache[url] = (time.time() + max_age, status_line, headers, body)

    # Follow 3xx redirects
    if is_redirect:
        if max_redirect == 0:
            raise RuntimeError("Too many redirects")
        location = headers["location"]
        if location.startswith("//"):
            location = scheme + ":" + location
        elif location.startswith("/"):
            port_part = f":{port}" if port not in (80, 443) else ""
            location = f"{scheme}://{host}{port_part}{location}"
        return request(location, max_redirect - 1)

    return status_line, headers, body


class TextExtractor(HTMLParser):
    """Collects visible text, skipping <script> and <style> blocks."""

    def __init__(self):
        super().__init__()
        self._skip = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._skip == 0:
            self._parts.append(data)

    def get_text(self):
        lines = []
        for part in self._parts:
            part = html_module.unescape(part)
            for line in part.splitlines():
                if line.strip():
                    lines.append(line)
        return "\n".join(lines)


def show(body_bytes, headers):
    """Decode body and print plain text, stripping HTML tags."""
    # view-source: print raw HTML without parsing
    if headers.get("_view_source"):
        print(body_bytes.decode("utf-8", errors="replace"))
        return

    content_type = headers.get("content-type", "")

    # Encoding resolution
    encoding = "utf-8"
    if "charset=" in content_type:
        encoding = content_type.split("charset=", 1)[1].split(";")[0].strip()

    candidates = [encoding] + [e for e in ["utf-8", "euc-kr", "latin-1"] if e != encoding]
    text = None
    for enc in candidates:
        try:
            text = body_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if text is None:
        raise ValueError("Unable to decode body with any supported encoding")

    if "html" not in content_type:
        print(text)
        return

    extractor = TextExtractor()
    extractor.feed(text)
    print(extractor.get_text())


def main():
    url = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_URL

    print("=== Request ===")
    print(f"URL: {url}")
    print()

    status_line, headers, body = request(url)

    print("=== Response Status ===")
    print(status_line)
    print()

    print("=== Response Headers ===")
    for key, value in headers.items():
        if not key.startswith("_"):  # skip internal markers like _view_source
            print(f"{key}: {value}")
    print()

    print("=== Body (text) ===")
    show(body, headers)


if __name__ == "__main__":
    main()
