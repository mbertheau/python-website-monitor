import time

import pytest
from aiohttp import web

from website_monitor.sense import (
    check_website,
    decode_http_response_body,
    prepare_website,
)


@pytest.mark.parametrize(
    "url,open_connection_args,http_request",
    [
        (
            "https://www.google.com",
            {
                "host": "www.google.com",
                "port": 443,
                "ssl": True,
            },
            b"GET / HTTP/1.0\r\nHost: www.google.com\r\n\r\n",
        ),
        # Test cases generated with the help of GitHub CoPilot.
        #
        # missing path - use /
        (
            "http://github.com",
            {"host": "github.com", "port": 80, "ssl": None},
            b"GET / HTTP/1.0\r\nHost: github.com\r\n\r\n",
        ),
        # non-standard port, no ssl
        (
            "http://github.com:8080",
            {"host": "github.com", "port": 8080, "ssl": None},
            b"GET / HTTP/1.0\r\nHost: github.com\r\n\r\n",
        ),
        # non-standard port, ssl
        (
            "https://github.com:8080",
            {"host": "github.com", "port": 8080, "ssl": True},
            b"GET / HTTP/1.0\r\nHost: github.com\r\n\r\n",
        ),
        # path with query
        (
            "https://github.com:8080/some/path?query=1",
            {"host": "github.com", "port": 8080, "ssl": True},
            b"GET /some/path?query=1 HTTP/1.0\r\nHost: github.com\r\n\r\n",
        ),
    ],
)
def test_prepare_website(url, open_connection_args, http_request):
    website = {"url": url}
    result = prepare_website(website)

    assert result["open_connection_args"] == open_connection_args
    assert result["http_request"] == http_request


@pytest.mark.parametrize(
    "body,decoded_body",
    [
        (b"some text", "some text"),
        ("äääää".encode("UTF-8"), "äääää"),
        # cut off bytes in the middle of a UTF-8 character
        ("äääää".encode("UTF-8")[:-1], "ääää"),
        ("äääää".encode("UTF-8")[:-2], "ääää"),
        ("äääää".encode("UTF-8")[:-3], "äää"),
        ("äääää".encode("UTF-8")[:-4], "äää"),
        # fallback to latin-1
        ("abc ä abc ä abc".encode("latin-1"), "abc ä abc ä abc"),
    ],
)
def test_decode_body(body, decoded_body):
    assert decode_http_response_body(body) == decoded_body


async def hello_world_response(request):
    return web.Response(body=b"Hello, world")


async def empty_response(request):
    return web.Response()


@pytest.mark.parametrize(
    "test_data",
    [
        {},
        {"response_regexp": "Hello"},
        {"response_regexp": "Guten Tag", "response_matches_regexp": False},
        {"host": "invalid", "error": "DNS lookup failed"},
        {
            "path": "/empty",
            "response_regexp": "Hello",
            "response_matches_regexp": False,
        },
    ],
)
@pytest.mark.asyncio
async def test_check_website(aiohttp_server, test_data):
    app = web.Application()
    app.router.add_route("GET", "/", hello_world_response)
    app.router.add_route("GET", "/empty", empty_response)

    server = await aiohttp_server(app)

    host = test_data.get("host", server.host)
    port = test_data.get("port", server.port)
    path = test_data.get("path", "")

    website = {"url": f"http://{host}:{port}{path}"}

    response_regexp = test_data.get("response_regexp")
    if response_regexp:
        website["response_regexp"] = response_regexp

    website = prepare_website(website)

    time_before = time.time()
    res = await check_website(website)
    time_after = time.time()

    assert time_before < res["request_time"] < time_after

    error = test_data.get("error")
    if error:
        assert res["error"] == error
    else:
        assert "error" not in res
        assert res["status_code"] == 200
        assert 0.0001 < res["response_duration"] < 0.1

    if response_regexp:
        assert res["response_regexp"] == response_regexp
        assert res["response_matches_regexp"] == test_data.get(
            "response_matches_regexp", True
        )
    else:
        assert "response_regexp" not in res
        assert "response_matches_regexp" not in res


@pytest.mark.asyncio
async def test_check_website_connection_refused(aiohttp_unused_port):
    website = {"url": f"http://localhost:{aiohttp_unused_port()}"}
    website = prepare_website(website)

    res = await check_website(website)

    assert res["error"] == "Connection failed"
