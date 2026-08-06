"""S5 contracts for proxy-mediated network policy."""

from __future__ import annotations

import asyncio
import base64
import socket

import pytest

from openharness.execution.network_proxy import (
    ManagedNetworkProxy,
    NetworkTarget,
    _origin_request_header,
    _parse_authority,
    _parse_http_target,
    _parse_proxy_header,
    _proxy_request_id,
    evaluate_network_target,
)
from openharness.permissions import NetworkPolicy


def test_public_allowlist_accepts_exact_domain_and_subdomains() -> None:
    policy = NetworkPolicy(enabled=True, allow_domains=("pypi.org",))

    assert (
        evaluate_network_target(
            NetworkTarget(host="pypi.org", port=443, addresses=("151.101.0.223",)),
            policy,
        )
        is None
    )
    assert (
        evaluate_network_target(
            NetworkTarget(host="files.pypi.org", port=443, addresses=("151.101.0.223",)),
            policy,
        )
        is None
    )


def test_domain_outside_allowlist_is_a_deterministic_violation() -> None:
    policy = NetworkPolicy(enabled=True, allow_domains=("pypi.org",))

    violation = evaluate_network_target(
        NetworkTarget(host="example.com", port=443, addresses=("93.184.216.34",)),
        policy,
    )

    assert violation is not None
    assert violation.dimension == "network.domain"
    assert violation.requested == "example.com:443"
    assert violation.evidence == "domain is not in the active allowlist"


def test_denylist_wins_over_allowlist() -> None:
    policy = NetworkPolicy(
        enabled=True,
        allow_domains=("example.com",),
        deny_domains=("blocked.example.com",),
    )

    violation = evaluate_network_target(
        NetworkTarget(
            host="blocked.example.com",
            port=443,
            addresses=("93.184.216.34",),
        ),
        policy,
    )

    assert violation is not None
    assert violation.dimension == "network.domain"
    assert violation.evidence == "domain is explicitly denied"


def test_private_loopback_and_link_local_addresses_are_denied_by_default() -> None:
    policy = NetworkPolicy(enabled=True, allow_domains=("service.example",))

    cases = (
        ("127.0.0.1", "network.loopback"),
        ("10.0.0.8", "network.private"),
        ("169.254.169.254", "network.link_local"),
        ("::1", "network.loopback"),
    )
    for address, dimension in cases:
        violation = evaluate_network_target(
            NetworkTarget(host="service.example", port=443, addresses=(address,)),
            policy,
        )
        assert violation is not None
        assert violation.dimension == dimension


def test_policy_can_explicitly_allow_private_address_classes() -> None:
    policy = NetworkPolicy(
        enabled=True,
        allow_domains=("service.example",),
        allow_loopback=True,
        allow_private=True,
        allow_link_local=True,
    )

    for address in ("127.0.0.1", "10.0.0.8", "169.254.169.254"):
        assert (
            evaluate_network_target(
                NetworkTarget(host="service.example", port=443, addresses=(address,)),
                policy,
            )
            is None
        )


def test_disabled_policy_is_a_typed_violation() -> None:
    violation = evaluate_network_target(
        NetworkTarget(host="example.com", port=443, addresses=("93.184.216.34",)),
        NetworkPolicy(),
    )
    assert violation is not None
    assert violation.dimension == "network.disabled"


def test_proxy_parsers_reject_ambiguous_requests_and_normalize_valid_ones() -> None:
    request_line, headers = _parse_proxy_header(
        b"GET http://example.com/a?q=1 HTTP/1.1\r\nHost: example.com\r\n\r\n"
    )
    assert request_line.startswith("GET ")
    assert headers == {"host": "example.com"}
    with pytest.raises(ValueError, match="request line"):
        _parse_proxy_header(b"BROKEN\r\n\r\n")
    with pytest.raises(ValueError, match="header"):
        _parse_proxy_header(b"GET http://x/ HTTP/1.1\r\nBroken\r\n\r\n")

    assert _parse_authority("example.com:443") == ("example.com", 443)
    assert _parse_authority("[::1]:8443") == ("::1", 8443)
    with pytest.raises(ValueError, match="host and port"):
        _parse_authority("example.com")
    with pytest.raises(ValueError, match="out of range"):
        _parse_authority("example.com:0")

    assert _parse_http_target("http://example.com?q=1") == (
        "example.com",
        80,
        "/?q=1",
    )
    with pytest.raises(ValueError, match="absolute http"):
        _parse_http_target("https://example.com/")


def test_proxy_auth_and_origin_header_never_forward_proxy_credentials() -> None:
    token = base64.b64encode(b"request-1:ignored").decode()
    assert _proxy_request_id({"proxy-authorization": f"Basic {token}"}) == "request-1"
    assert _proxy_request_id({}) == "unattributed"
    assert _proxy_request_id({"proxy-authorization": "Basic !!!"}) == "unattributed"
    assert (
        _proxy_request_id({"proxy-authorization": f"Basic {base64.b64encode(b':x').decode()}"})
        == "unattributed"
    )
    header = _origin_request_header(
        method="GET",
        target="/",
        version="HTTP/1.1",
        headers={
            "host": "example.com",
            "proxy-authorization": "secret",
            "proxy-connection": "keep-alive",
        },
    )
    assert b"host: example.com" in header
    assert b"proxy-" not in header.lower()


async def test_proxy_rejects_disabled_policy_and_bad_request_ids() -> None:
    with pytest.raises(ValueError, match="enabled"):
        await ManagedNetworkProxy.open(NetworkPolicy())
    proxy = await ManagedNetworkProxy.open(NetworkPolicy(enabled=True))
    try:
        assert proxy.url_for("request-ok").startswith("http://request-ok:x@127.0.0.1:")
        for request_id in ("", "a:b", "a/b", "a@b"):
            with pytest.raises(ValueError, match="delimiters"):
                proxy.url_for(request_id)
    finally:
        await proxy.close()


async def test_malformed_request_and_upstream_refusal_are_transport_errors() -> None:
    proxy = await ManagedNetworkProxy.open(
        NetworkPolicy(enabled=True, allow_domains=("localhost",), allow_loopback=True)
    )
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    unused_port = int(probe.getsockname()[1])
    probe.close()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"BROKEN\r\n\r\n")
        await writer.drain()
        assert (await reader.read()).startswith(b"HTTP/1.1 400")
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(_connect_request(host="localhost", port=unused_port, request_id="refused"))
        await writer.drain()
        assert (await reader.read()).startswith(b"HTTP/1.1 502")
        assert proxy.violations_for("refused") == ()
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


async def test_address_class_denial_after_dns_is_recorded() -> None:
    proxy = await ManagedNetworkProxy.open(
        NetworkPolicy(enabled=True, allow_domains=("localhost",))
    )
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(_connect_request(host="localhost", port=80, request_id="dns-class"))
        await writer.drain()
        assert (await reader.read()).startswith(b"HTTP/1.1 403")
        violations = proxy.violations_for("dns-class")
        assert violations[0].dimension == "network.loopback"
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


def _connect_request(*, host: str, port: int, request_id: str) -> bytes:
    credentials = base64.b64encode(f"{request_id}:x".encode()).decode()
    return (
        f"CONNECT {host}:{port} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Proxy-Authorization: Basic {credentials}\r\n\r\n"
    ).encode()


async def test_managed_proxy_allows_an_explicit_loopback_target() -> None:
    async def _upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(16)
        writer.write(data.upper())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(_upstream, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])
    proxy = await ManagedNetworkProxy.open(
        NetworkPolicy(
            enabled=True,
            allow_domains=("localhost",),
            allow_loopback=True,
        )
    )
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(_connect_request(host="localhost", port=upstream_port, request_id="allowed-1"))
        await writer.drain()
        response = await reader.readuntil(b"\r\n\r\n")
        assert response.startswith(b"HTTP/1.1 200")
        writer.write(b"hello")
        await writer.drain()
        assert await reader.read(5) == b"HELLO"
        assert proxy.violations_for("allowed-1") == ()
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
        upstream.close()
        await upstream.wait_closed()


async def test_managed_proxy_records_policy_denial_as_typed_violation() -> None:
    proxy = await ManagedNetworkProxy.open(
        NetworkPolicy(enabled=True, allow_domains=("example.com",))
    )
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(_connect_request(host="localhost", port=80, request_id="blocked-1"))
        await writer.drain()
        response = await reader.readuntil(b"\r\n\r\n")
        assert response.startswith(b"HTTP/1.1 403")
        violations = proxy.violations_for("blocked-1")
        assert len(violations) == 1
        assert violations[0].dimension == "network.domain"
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


async def test_managed_proxy_forwards_plain_http_absolute_form() -> None:
    received: list[bytes] = []

    async def _upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        received.append(request)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(_upstream, "127.0.0.1", 0)
    upstream_port = int(upstream.sockets[0].getsockname()[1])
    proxy = await ManagedNetworkProxy.open(
        NetworkPolicy(enabled=True, allow_domains=("localhost",), allow_loopback=True)
    )
    try:
        credentials = base64.b64encode(b"http-1:x").decode()
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(
            (
                f"GET http://localhost:{upstream_port}/simple?q=1 HTTP/1.1\r\n"
                f"Host: localhost:{upstream_port}\r\n"
                f"Proxy-Authorization: Basic {credentials}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        response = await reader.read()

        assert response.startswith(b"HTTP/1.1 200 OK")
        assert received[0].startswith(b"GET /simple?q=1 HTTP/1.1\r\n")
        assert b"Proxy-Authorization" not in received[0]
        assert proxy.violations_for("http-1") == ()
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
        upstream.close()
        await upstream.wait_closed()


async def test_dns_failure_is_not_mislabeled_as_a_permission_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(*args: object, **kwargs: object) -> object:
        raise socket.gaierror("missing")

    monkeypatch.setattr(socket, "getaddrinfo", _missing)
    proxy = await ManagedNetworkProxy.open(
        NetworkPolicy(enabled=True, allow_domains=("definitely-missing.invalid",))
    )
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(
            _connect_request(
                host="definitely-missing.invalid",
                port=443,
                request_id="dns-failure",
            )
        )
        await writer.drain()
        response = await reader.readuntil(b"\r\n\r\n")
        assert response.startswith(b"HTTP/1.1 502")
        assert proxy.violations_for("dns-failure") == ()
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
