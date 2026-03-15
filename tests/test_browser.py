import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from browser import parse_url

def test_http_defaults():
    scheme, host, port, path = parse_url("http://example.com")
    assert scheme == "http"
    assert host == "example.com"
    assert port == 80
    assert path == "/"

def test_https_defaults():
    scheme, host, port, path = parse_url("https://example.com")
    assert scheme == "https"
    assert port == 443

def test_explicit_port():
    scheme, host, port, path = parse_url("http://example.com:8080/foo")
    assert port == 8080
    assert host == "example.com"
    assert path == "/foo"

def test_path_and_query():
    _, _, _, path = parse_url("https://example.com/search?q=hello")
    assert path == "/search?q=hello"

def test_fragment_stripped():
    _, host, _, path = parse_url("https://example.com/page#section")
    assert path == "/page"
    assert "#" not in host

def test_no_path_defaults_to_slash():
    _, _, _, path = parse_url("https://example.com")
    assert path == "/"

from unittest.mock import patch, MagicMock

def _make_mock_sock(response_bytes):
    """Return a MagicMock socket that yields response_bytes then EOF."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [response_bytes, b""]
    return mock_sock

def test_request_returns_tuple():
    """request() should return (status_line, headers_dict, body_bytes)."""
    mock_response = (
        b"HTTP/1.0 200 OK\r\n"
        b"Content-Type: text/html; charset=UTF-8\r\n"
        b"\r\n"
        b"<html><body>Hello</body></html>"
    )
    mock_sock = _make_mock_sock(mock_response)

    with patch("socket.socket", return_value=mock_sock):
        from browser import request
        status, headers, body = request("http://example.com")

    assert status == "HTTP/1.0 200 OK"
    assert headers["content-type"] == "text/html; charset=UTF-8"
    assert body == b"<html><body>Hello</body></html>"

def test_headers_are_lowercased():
    mock_response = b"HTTP/1.0 200 OK\r\nX-Custom-Header: Value\r\n\r\nbody"
    mock_sock = _make_mock_sock(mock_response)

    with patch("socket.socket", return_value=mock_sock):
        from browser import request
        _, headers, _ = request("http://example.com")

    assert "x-custom-header" in headers

def test_redirect_followed():
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
        from browser import request
        status, _, body = request("http://example.com/old")

    assert status == "HTTP/1.0 200 OK"
    assert body == b"<p>Final</p>"

def test_redirect_limit_raises():
    """max_redirect=0 with a 3xx response should raise RuntimeError immediately."""
    redirect_response = (
        b"HTTP/1.0 301 Moved Permanently\r\n"
        b"Location: http://example.com/new\r\n"
        b"\r\n"
    )
    import pytest
    mock_sock = _make_mock_sock(redirect_response)
    with patch("socket.socket", return_value=mock_sock):
        from browser import request
        with pytest.raises(RuntimeError, match="Too many redirects"):
            request("http://example.com/old", max_redirect=0)


import io
from contextlib import redirect_stdout

def _capture_show(body_bytes, headers):
    from browser import show
    f = io.StringIO()
    with redirect_stdout(f):
        show(body_bytes, headers)
    return f.getvalue()

def test_show_extracts_text():
    html = b"<html><body><p>Hello World</p></body></html>"
    out = _capture_show(html, {"content-type": "text/html"})
    assert "Hello World" in out

def test_show_skips_script():
    html = b"<html><body><script>alert(1)</script><p>Visible</p></body></html>"
    out = _capture_show(html, {"content-type": "text/html"})
    assert "alert" not in out
    assert "Visible" in out

def test_show_skips_style():
    html = b"<html><head><style>body{color:red}</style></head><body>Text</body></html>"
    out = _capture_show(html, {"content-type": "text/html"})
    assert "color" not in out
    assert "Text" in out

def test_show_handles_utf8_encoding():
    html = "안녕하세요".encode("utf-8")
    out = _capture_show(html, {"content-type": "text/html; charset=utf-8"})
    assert "안녕하세요" in out

def test_show_handles_euckr_fallback():
    html = b"<p>" + "안녕".encode("euc-kr") + b"</p>"
    out = _capture_show(html, {"content-type": "text/html"})
    assert "안녕" in out

def test_show_non_html_prints_raw():
    body = b"{'key': 'value'}"
    out = _capture_show(body, {"content-type": "application/json"})
    assert "key" in out
