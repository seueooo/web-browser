import socket
import ssl
import sys
from html.parser import HTMLParser


def parse_url(url):
    """Return (scheme, host, port, path) from a URL string."""
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
    # Path includes query string already
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


def request(url, max_redirect=10):
    """Fetch URL, return (status_line, headers_dict, body_bytes).
    Follows 3xx redirects up to max_redirect times.
    """
    scheme, host, port, path = parse_url(url)

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_sock.settimeout(10)
    if scheme == "https":
        ctx = ssl.create_default_context()
        s = ctx.wrap_socket(raw_sock, server_hostname=host)
    else:
        s = raw_sock

    try:
        s.connect((host, port))

        # Send HTTP/1.0 request
        req = f"GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n"
        s.sendall(req.encode("utf-8"))

        # Read full response until EOF
        raw = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            raw += chunk
    finally:
        s.close()  # closes the SSL wrapper (and underlying socket) if HTTPS

    # Split headers from body on first blank line
    header_section, _, body = raw.partition(b"\r\n\r\n")
    header_lines = header_section.decode("utf-8", errors="replace").split("\r\n")

    status_line = header_lines[0]
    headers = {}
    for line in header_lines[1:]:
        if ": " in line:
            key, value = line.split(": ", 1)
            headers[key.lower()] = value

    # Follow 3xx redirects
    status_code = status_line.split(" ", 2)[1] if " " in status_line else ""
    if status_code.startswith("3") and "location" in headers:
        if max_redirect == 0:
            raise RuntimeError("Too many redirects")
        location = headers["location"]
        # Resolve relative redirects to absolute URLs
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
        self._skip = 0      # counter, not bool — handles nesting correctly
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
            for line in part.splitlines():
                if line.strip():
                    lines.append(line)
        return "\n".join(lines)


def show(body_bytes, headers):
    """Decode body and print plain text, stripping HTML tags."""
    content_type = headers.get("content-type", "")

    # Encoding resolution
    encoding = "utf-8"
    if "charset=" in content_type:
        encoding = content_type.split("charset=", 1)[1].split(";")[0].strip()

    # Decode with fallback chain (deduplicated so declared encoding isn't tried twice)
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
    if len(sys.argv) < 2:
        print("Usage: browser.py <url>")
        sys.exit(1)

    url = sys.argv[1]

    # Show what will be requested
    print("=== Request ===")
    print(f"URL: {url}")
    print()

    status_line, headers, body = request(url)

    # Show response status
    print("=== Response Status ===")
    print(status_line)
    print()

    # Show all response headers
    print("=== Response Headers ===")
    for key, value in headers.items():
        print(f"{key}: {value}")
    print()

    # Show parsed body text
    print("=== Body (text) ===")
    show(body, headers)


if __name__ == "__main__":
    main()
