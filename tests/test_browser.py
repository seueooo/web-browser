import sys
import os
import gzip as gzip_module
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from browser import URL, HttpClient, decode_body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Fresh HttpClient for each test."""
    return HttpClient()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_sock(response_bytes):
    """Return a MagicMock socket that yields response_bytes then EOF."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [response_bytes, b""]
    return mock_sock


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------

def test_http_defaults():
    u = URL("http://example.com")
    assert u.scheme == "http"
    assert u.host == "example.com"
    assert u.port == 80
    assert u.path == "/"


def test_https_defaults():
    u = URL("https://example.com")
    assert u.scheme == "https"
    assert u.port == 443


def test_explicit_port():
    u = URL("http://example.com:8080/foo")
    assert u.port == 8080
    assert u.host == "example.com"
    assert u.path == "/foo"


def test_path_and_query():
    u = URL("https://example.com/search?q=hello")
    assert u.path == "/search?q=hello"


def test_fragment_stripped():
    u = URL("https://example.com/page#section")
    assert u.path == "/page"
    assert "#" not in u.host


def test_no_path_defaults_to_slash():
    u = URL("https://example.com")
    assert u.path == "/"


def test_parse_url_file_scheme():
    u = URL("file:///tmp/test.html")
    assert u.scheme == "file"
    assert u.path == "/tmp/test.html"


def test_url_without_scheme_defaults_to_http():
    u = URL("example.com")
    assert u.scheme == "http"
    assert u.host == "example.com"


def test_url_is_network():
    assert URL("http://example.com").is_network is True
    assert URL("https://example.com").is_network is True
    assert URL("file:///tmp/x").is_network is False


def test_resolve_redirect_absolute():
    u = URL("http://example.com/old")
    assert u.resolve_redirect("http://other.com/new") == "http://other.com/new"


def test_resolve_redirect_relative():
    u = URL("http://example.com/old")
    assert u.resolve_redirect("/new") == "http://example.com/new"


def test_resolve_redirect_protocol_relative():
    u = URL("https://example.com/old")
    assert u.resolve_redirect("//other.com/new") == "https://other.com/new"


# ---------------------------------------------------------------------------
# HttpClient.request() — HTTP/1.1 headers
# ---------------------------------------------------------------------------

def test_request_sends_http11(client):
    """request() must use HTTP/1.1 and send Host, Connection, User-Agent, Accept-Encoding."""
    sent = []
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nHello",
        b""
    ]
    mock_sock.sendall.side_effect = lambda data: sent.append(data.decode())

    with patch("socket.socket", return_value=mock_sock):
        client.request("http://example.com/")

    req_text = "".join(sent)
    assert "HTTP/1.1" in req_text
    assert "Host: example.com" in req_text
    assert "Connection: keep-alive" in req_text
    assert "User-Agent:" in req_text
    assert "Accept-Encoding: gzip" in req_text


def test_request_returns_tuple(client):
    """request() should return (status_line, headers_dict, body_bytes)."""
    mock_response = (
        b"HTTP/1.0 200 OK\r\n"
        b"Content-Type: text/html; charset=UTF-8\r\n"
        b"\r\n"
        b"<html><body>Hello</body></html>"
    )
    mock_sock = _make_mock_sock(mock_response)

    with patch("socket.socket", return_value=mock_sock):
        status, headers, body = client.request("http://example.com")

    assert status == "HTTP/1.0 200 OK"
    assert headers["content-type"] == "text/html; charset=UTF-8"
    assert body == b"<html><body>Hello</body></html>"


def test_headers_are_lowercased(client):
    mock_response = b"HTTP/1.0 200 OK\r\nX-Custom-Header: Value\r\n\r\nbody"
    mock_sock = _make_mock_sock(mock_response)

    with patch("socket.socket", return_value=mock_sock):
        _, headers, _ = client.request("http://example.com")

    assert "x-custom-header" in headers


# ---------------------------------------------------------------------------
# HttpClient.request() — file:// scheme
# ---------------------------------------------------------------------------

def test_request_file_scheme(client, tmp_path):
    """file:// URL should read a local file."""
    f = tmp_path / "hello.html"
    f.write_bytes(b"<p>Hello file</p>")
    status, headers, body = client.request(f"file://{f}")
    assert status == "200 OK"
    assert body == b"<p>Hello file</p>"


# ---------------------------------------------------------------------------
# HttpClient.request() — data: scheme
# ---------------------------------------------------------------------------

def test_request_data_scheme_text(client):
    status, headers, body = client.request("data:text/html,Hello world!")
    assert status == "200 OK"
    assert headers["content-type"] == "text/html"
    assert body == b"Hello world!"


def test_request_data_scheme_default_type(client):
    status, headers, body = client.request("data:,plain text")
    assert status == "200 OK"
    assert b"plain text" in body


# ---------------------------------------------------------------------------
# HttpClient.request() — view-source: scheme
# ---------------------------------------------------------------------------

def test_request_view_source(client):
    """view-source: scheme fetches the URL and marks headers for raw display."""
    mock_response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/html\r\n"
        b"Content-Length: 30\r\n"
        b"\r\n"
        b"<html><body>Hello</body></html>"
    )
    mock_sock = MagicMock()
    pos = [0]
    def fake_recv(n):
        chunk = mock_response[pos[0]:pos[0]+n]
        pos[0] += len(chunk)
        return chunk
    mock_sock.recv.side_effect = fake_recv

    with patch("socket.socket", return_value=mock_sock):
        status, headers, body = client.request("view-source:http://example.com/")
    assert headers.get("_view_source") is True
    assert b"<html>" in body


# ---------------------------------------------------------------------------
# HttpClient.request() — keep-alive socket pool
# ---------------------------------------------------------------------------

def test_keepalive_reuses_socket(client):
    """Two requests to the same host should reuse the same socket."""
    combined = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Type: text/html\r\n\r\nHello"
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Type: text/html\r\n\r\nWorld"
    )
    mock_sock = MagicMock()
    pos = [0]
    def fake_recv(n):
        chunk = combined[pos[0]:pos[0]+n]
        pos[0] += len(chunk)
        return chunk
    mock_sock.recv.side_effect = fake_recv

    with patch("socket.socket", return_value=mock_sock) as mock_ctor:
        client.request("http://example.com/a")
        client.request("http://example.com/b")
    assert mock_ctor.call_count == 1


def test_keepalive_reads_content_length_only(client):
    """Should read only Content-Length bytes, leaving the rest for the next response."""
    combined = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Type: text/html\r\n\r\nHello"
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Type: text/html\r\n\r\nWorld"
    )
    mock_sock = MagicMock()
    pos = [0]
    def fake_recv(n):
        chunk = combined[pos[0]:pos[0]+n]
        pos[0] += len(chunk)
        return chunk
    mock_sock.recv.side_effect = fake_recv

    with patch("socket.socket", return_value=mock_sock):
        _, _, body1 = client.request("http://example.com/a")
        _, _, body2 = client.request("http://example.com/b")
    assert body1 == b"Hello"
    assert body2 == b"World"


# ---------------------------------------------------------------------------
# HttpClient.request() — redirects
# ---------------------------------------------------------------------------

def test_redirect_followed(client):
    """301 response should cause request() to fetch the Location URL."""
    redirect_response = (
        b"HTTP/1.0 301 Moved Permanently\r\n"
        b"Location: http://example.com/new\r\n"
        b"\r\n"
    )
    final_response = (
        b"HTTP/1.0 200 OK\r\n"
        b"Content-Type: text/html\r\n"
        b"\r\n"
        b"<p>Final</p>"
    )
    sock1 = _make_mock_sock(redirect_response)
    sock2 = _make_mock_sock(final_response)

    with patch("socket.socket", side_effect=[sock1, sock2]):
        status, _, body = client.request("http://example.com/old")

    assert status == "HTTP/1.0 200 OK"
    assert body == b"<p>Final</p>"


def test_redirect_limit_raises(client):
    """max_redirect=0 with a 3xx response should raise RuntimeError immediately."""
    redirect_response = (
        b"HTTP/1.0 301 Moved Permanently\r\n"
        b"Location: http://example.com/new\r\n"
        b"\r\n"
    )
    mock_sock = _make_mock_sock(redirect_response)
    with patch("socket.socket", return_value=mock_sock):
        with pytest.raises(RuntimeError, match="Too many redirects"):
            client.request("http://example.com/old", max_redirect=0)


def test_redirect_relative_path(client):
    """A Location starting with / should be resolved against the original host."""
    redirect_response = (
        b"HTTP/1.1 302 Found\r\n"
        b"Location: /new-path\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )
    final_response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: 8\r\n"
        b"Content-Type: text/html\r\n"
        b"\r\n"
        b"Arrived!"
    )
    sock1 = MagicMock()
    pos1 = [0]
    def recv1(n):
        chunk = redirect_response[pos1[0]:pos1[0]+n]
        pos1[0] += len(chunk)
        return chunk
    sock1.recv.side_effect = recv1

    sock2 = MagicMock()
    pos2 = [0]
    def recv2(n):
        chunk = final_response[pos2[0]:pos2[0]+n]
        pos2[0] += len(chunk)
        return chunk
    sock2.recv.side_effect = recv2

    with patch("socket.socket", side_effect=[sock1, sock2]):
        _, _, body = client.request("http://example.com/old")
    assert b"Arrived!" in body


# ---------------------------------------------------------------------------
# HttpClient.request() — caching
# ---------------------------------------------------------------------------

def test_cache_stores_and_returns_200(client):
    """200 responses with max-age should be served from cache on repeat requests."""
    mock_response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: 5\r\n"
        b"Content-Type: text/html\r\n"
        b"Cache-Control: max-age=60\r\n"
        b"\r\n"
        b"Hello"
    )
    mock_sock = MagicMock()
    pos = [0]
    def fake_recv(n):
        chunk = mock_response[pos[0]:pos[0]+n]
        pos[0] += len(chunk)
        return chunk
    mock_sock.recv.side_effect = fake_recv

    with patch("socket.socket", return_value=mock_sock) as mock_ctor:
        client.request("http://example.com/cached")
        client.request("http://example.com/cached")
    assert mock_ctor.call_count == 1


def test_cache_respects_no_store(client):
    """Cache-Control: no-store must not populate the cache."""
    combined = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nCache-Control: no-store\r\n\r\nHello"
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nCache-Control: no-store\r\n\r\nWorld"
    )
    mock_sock = MagicMock()
    pos = [0]
    def fake_recv(n):
        chunk = combined[pos[0]:pos[0]+n]
        pos[0] += len(chunk)
        return chunk
    mock_sock.recv.side_effect = fake_recv

    with patch("socket.socket", return_value=mock_sock):
        client.request("http://example.com/nocache")
    assert "http://example.com/nocache" not in client.cache


def test_cache_expires(client):
    """Cached responses should be re-fetched after max-age seconds have elapsed."""
    combined = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nCache-Control: max-age=1\r\n\r\nHello"
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nCache-Control: max-age=1\r\n\r\nWorld"
    )
    mock_sock = MagicMock()
    pos = [0]
    def fake_recv(n):
        chunk = combined[pos[0]:pos[0]+n]
        pos[0] += len(chunk)
        return chunk
    mock_sock.recv.side_effect = fake_recv

    sent_count = [0]
    mock_sock.sendall.side_effect = lambda d: sent_count.__setitem__(0, sent_count[0] + 1)

    with patch("socket.socket", return_value=mock_sock):
        with patch("time.time", side_effect=[0, 2, 2]):
            client.request("http://example.com/expire")
            client.request("http://example.com/expire")
    assert sent_count[0] == 2


# ---------------------------------------------------------------------------
# HttpClient.request() — gzip + chunked
# ---------------------------------------------------------------------------

def test_request_accepts_gzip(client):
    """Request headers must include Accept-Encoding: gzip."""
    sent = []
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nHello", b""
    ]
    mock_sock.sendall.side_effect = lambda d: sent.append(d.decode())
    with patch("socket.socket", return_value=mock_sock):
        client.request("http://example.com/")
    assert "Accept-Encoding: gzip" in "".join(sent)


def test_request_decompresses_gzip_body(client):
    """Content-Encoding: gzip responses must be automatically decompressed."""
    compressed = gzip_module.compress(b"Hello gzip!")
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Encoding: gzip\r\n"
        + f"Content-Length: {len(compressed)}\r\n".encode()
        + b"Content-Type: text/html\r\n"
        b"\r\n"
        + compressed
    )
    mock_sock = MagicMock()
    pos = [0]
    def fake_recv(n):
        chunk = response[pos[0]:pos[0]+n]
        pos[0] += len(chunk)
        return chunk
    mock_sock.recv.side_effect = fake_recv
    with patch("socket.socket", return_value=mock_sock):
        _, _, body = client.request("http://example.com/gz")
    assert body == b"Hello gzip!"


def test_request_handles_chunked(client):
    """Transfer-Encoding: chunked responses must be correctly reassembled."""
    chunked_body = b"5\r\nHello\r\n6\r\n World\r\n0\r\n\r\n"
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Content-Type: text/html\r\n"
        b"\r\n"
        + chunked_body
    )
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [response, b""]
    with patch("socket.socket", return_value=mock_sock):
        _, _, body = client.request("http://example.com/chunked")
    assert body == b"Hello World"


# ---------------------------------------------------------------------------
# decode_body()
# ---------------------------------------------------------------------------

def test_decode_body_utf8():
    body = "안녕하세요".encode("utf-8")
    text = decode_body(body, {"content-type": "text/html; charset=utf-8"})
    assert "안녕하세요" in text


def test_decode_body_euckr_fallback():
    body = "안녕".encode("euc-kr")
    text = decode_body(body, {"content-type": "text/html"})
    assert "안녕" in text


def test_decode_body_plain():
    body = b"{'key': 'value'}"
    text = decode_body(body, {"content-type": "application/json"})
    assert "key" in text
