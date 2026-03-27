import socket
import ssl
import gzip
import time


class URL:
    """URL 파싱 및 scheme별 분기"""

    def __init__(self, raw: str):
        self.raw = raw
        self.scheme, self.host, self.port, self.path = self._parse(raw)

    @staticmethod
    def _parse(url):
        if url.startswith("file://"):
            return "file", "", 0, url[len("file://"):]
        if url.startswith("data:"):
            return "data", "", 0, url[len("data:"):]
        if url.startswith("view-source:"):
            return "view-source", "", 0, url[len("view-source:"):]

        if "://" not in url:
            url = "http://" + url
        scheme, rest = url.split("://", 1)

        if "#" in rest:
            rest = rest[:rest.index("#")]
        if "/" in rest:
            host_port, path = rest.split("/", 1)
            path = "/" + path
        else:
            host_port = rest
            path = "/"

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

    @property
    def is_network(self) -> bool:
        return self.scheme in ("http", "https")

    @property
    def pool_key(self) -> tuple:
        return (self.scheme, self.host, self.port)

    def resolve_redirect(self, location: str) -> str:
        if location.startswith("//"):
            return self.scheme + ":" + location
        elif location.startswith("/"):
            port_part = f":{self.port}" if self.port not in (80, 443) else ""
            return f"{self.scheme}://{self.host}{port_part}{location}"
        return location


class Connection:
    """소켓 생성 + keep-alive 풀링"""

    def __init__(self):
        self._pool: dict = {}  # pool_key -> (socket, excess_bytes)

    def get(self, url: URL):
        """풀에서 꺼내거나 새로 생성. (socket, init_buffer) 반환"""
        entry = self._pool.pop(url.pool_key, None)
        if entry is not None:
            return entry

        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(10)
        if url.scheme == "https":
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(raw_sock, server_hostname=url.host)
        else:
            s = raw_sock
        s.connect((url.host, url.port))
        return s, b""

    def release(self, url: URL, sock, excess: bytes):
        self._pool[url.pool_key] = (sock, excess)

    def discard(self, sock):
        try:
            sock.close()
        except OSError:
            pass

    def clear(self):
        self._pool.clear()


class HttpClient:
    """HTTP 요청, 캐시, 리다이렉트, 바디 디코딩"""

    def __init__(self):
        self.conn = Connection()
        self.cache: dict = {}  # url_str -> (expires_at, status_line, headers, body)

    def request(self, raw_url: str, max_redirect=10):
        """(status_line, headers_dict, body_bytes) 반환"""
        url = URL(raw_url)

        # -- file:// --
        if url.scheme == "file":
            with open(url.path, "rb") as f:
                body = f.read()
            return "200 OK", {"content-type": "text/html"}, body

        # -- data: --
        if url.scheme == "data":
            meta, _, content = url.path.partition(",")
            content_type = meta if meta else "text/plain"
            return "200 OK", {"content-type": content_type}, content.encode("utf-8")

        # -- view-source: --
        if url.scheme == "view-source":
            _, inner_headers, inner_body = self.request(url.path, max_redirect)
            inner_headers["_view_source"] = True
            return "200 OK", inner_headers, inner_body

        # -- 캐시 조회 --
        if raw_url in self.cache:
            expires_at, cached_status, cached_headers, cached_body = self.cache[raw_url]
            if time.time() < expires_at:
                return cached_status, cached_headers, cached_body
            else:
                del self.cache[raw_url]

        # -- 소켓 연결 --
        s, init_buffer = self.conn.get(url)

        # -- 요청 전송 --
        req = self._build_request(url)
        print(f"\n{'='*60}")
        print(f">>> HTTP REQUEST >>>")
        print(f"{'='*60}")
        print(req.rstrip())
        print(f"{'='*60}\n")
        s.sendall(req.encode("utf-8"))

        # -- 응답 헤더 읽기 --
        status_line, headers, leftover = self._read_headers(s, init_buffer)
        print(f"{'='*60}")
        print(f"<<< HTTP RESPONSE <<<")
        print(f"{'='*60}")
        print(f"{status_line}")
        for k, v in headers.items():
            print(f"{k}: {v}")
        print(f"{'='*60}\n")

        status_code = status_line.split(" ", 2)[1] if " " in status_line else ""
        is_redirect = status_code.startswith("3") and "location" in headers

        # -- 바디 읽기 --
        body = self._read_body(s, url, headers, leftover, is_redirect)

        # -- gzip 해제 --
        if headers.get("content-encoding", "").lower() == "gzip":
            body = gzip.decompress(body)

        # -- 캐시 저장 --
        if status_code == "200":
            self._cache_response(raw_url, status_line, headers, body)

        # -- 리다이렉트 --
        if is_redirect:
            if max_redirect == 0:
                raise RuntimeError("Too many redirects")
            location = url.resolve_redirect(headers["location"])
            return self.request(location, max_redirect - 1)

        return status_line, headers, body

    # -- private helpers --

    @staticmethod
    def _build_request(url: URL) -> str:
        headers = {
            "Host": url.host,
            "Connection": "keep-alive",
            "User-Agent": "SimpleBrowser/1.0",
            "Accept-Encoding": "gzip",
        }
        header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
        return f"GET {url.path} HTTP/1.1\r\n{header_lines}\r\n\r\n"

    @staticmethod
    def _read_headers(sock, init_buffer: bytes):
        """(status_line, headers_dict, leftover_bytes) 반환"""
        raw = init_buffer
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        header_section, _, leftover = raw.partition(b"\r\n\r\n")
        lines = header_section.decode("utf-8", errors="replace").split("\r\n")
        status_line = lines[0]
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k.lower()] = v
        return status_line, headers, leftover

    def _read_body(self, sock, url, headers, leftover, is_redirect):
        content_length = int(headers.get("content-length", -1))
        is_chunked = headers.get("transfer-encoding", "").lower() == "chunked"
        body = leftover

        if content_length >= 0:
            while len(body) < content_length:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body += chunk
            excess = body[content_length:]
            body = body[:content_length]
            if not is_redirect:
                self.conn.release(url, sock, excess)
        elif is_chunked:
            while b"0\r\n\r\n" not in body:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body += chunk
            term_idx = body.find(b"0\r\n\r\n")
            if term_idx >= 0:
                excess = body[term_idx + 5:]
                body = body[:term_idx + 5]
            else:
                excess = b""
            body = _decode_chunked(body)
            if not is_redirect:
                self.conn.release(url, sock, excess)
        else:
            sock.settimeout(3)
            while True:
                try:
                    chunk = sock.recv(4096)
                except (TimeoutError, OSError):
                    break
                if not chunk:
                    break
                body += chunk
            self.conn.discard(sock)

        return body

    def _cache_response(self, raw_url, status_line, headers, body):
        cache_control = headers.get("cache-control", "")
        if "no-store" in cache_control:
            return
        max_age = None
        for part in cache_control.split(","):
            part = part.strip()
            if part.startswith("max-age="):
                try:
                    max_age = int(part[len("max-age="):])
                except ValueError:
                    pass
        if max_age is not None:
            self.cache[raw_url] = (time.time() + max_age, status_line, headers, body)

    @staticmethod
    def decode_body(body: bytes, headers: dict) -> str:
        """charset 감지 + 디코딩. gui.py에서도 호출."""
        content_type = headers.get("content-type", "")
        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=", 1)[1].split(";")[0].strip()

        candidates = [encoding] + [e for e in ["utf-8", "euc-kr", "latin-1"] if e != encoding]
        for enc in candidates:
            try:
                return body.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        raise ValueError("Unable to decode body with any supported encoding")


def _decode_chunked(data: bytes) -> bytes:
    """chunked transfer-encoding 디코딩"""
    result = b""
    while data:
        newline = data.index(b"\r\n")
        size = int(data[:newline], 16)
        if size == 0:
            break
        result += data[newline + 2: newline + 2 + size]
        data = data[newline + 2 + size + 2:]
    return result
