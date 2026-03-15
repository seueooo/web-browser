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
        return request(headers["location"], max_redirect - 1)

    return status_line, headers, body
