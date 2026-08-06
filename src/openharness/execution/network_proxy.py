"""Network-policy evaluation and the managed loopback proxy substrate.

Policy decisions live here rather than in command-output parsing.  The proxy
records denials as typed :class:`BoundaryViolation` values; DNS and upstream
transport failures remain ordinary network failures.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

from openharness.execution.boundary import BoundaryViolation

if TYPE_CHECKING:
    from openharness.permissions.profile import NetworkPolicy


@dataclass(frozen=True)
class NetworkTarget:
    host: str
    port: int
    addresses: tuple[str, ...]


def _normalized_domain(value: str) -> str:
    return value.rstrip(".").encode("idna").decode("ascii").lower()


def _domain_matches(host: str, rule: str) -> bool:
    normalized_host = _normalized_domain(host)
    normalized_rule = _normalized_domain(rule.lstrip("*."))
    return normalized_host == normalized_rule or normalized_host.endswith(f".{normalized_rule}")


def evaluate_network_target(
    target: NetworkTarget,
    policy: NetworkPolicy,
) -> BoundaryViolation | None:
    """Return a deterministic policy denial, or ``None`` when allowed.

    Every resolved address must satisfy the address-class policy.  This avoids
    permitting a hostname whose DNS answer mixes public and local addresses.
    """
    requested = f"{target.host}:{target.port}"
    if not policy.enabled:
        return BoundaryViolation(
            dimension="network.disabled",
            requested=requested,
            evidence="network access is disabled by the active profile",
        )
    if any(_domain_matches(target.host, rule) for rule in policy.deny_domains):
        return BoundaryViolation(
            dimension="network.domain",
            requested=requested,
            evidence="domain is explicitly denied",
            hard_deny=True,
        )
    if policy.allow_domains and not any(
        _domain_matches(target.host, rule) for rule in policy.allow_domains
    ):
        return BoundaryViolation(
            dimension="network.domain",
            requested=requested,
            evidence="domain is not in the active allowlist",
        )
    for raw_address in target.addresses:
        address = ipaddress.ip_address(raw_address)
        if address.is_loopback:
            if not policy.allow_loopback:
                return BoundaryViolation(
                    dimension="network.loopback",
                    requested=requested,
                    evidence=f"resolved address {address} is loopback",
                )
            continue
        if address.is_link_local:
            if not policy.allow_link_local:
                return BoundaryViolation(
                    dimension="network.link_local",
                    requested=requested,
                    evidence=f"resolved address {address} is link-local",
                )
            continue
        if address.is_private and not policy.allow_private:
            return BoundaryViolation(
                dimension="network.private",
                requested=requested,
                evidence=f"resolved address {address} is private",
            )
    return None


_MAX_PROXY_HEADER_BYTES = 64 * 1024
_MAX_TLS_CLIENT_HELLO_BYTES = 64 * 1024


class _ListeningServer(Protocol):
    @property
    def sockets(self) -> tuple[socket.socket, ...] | None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


class NetworkProxySession(Protocol):
    @property
    def port(self) -> int: ...

    def url_for(self, request_id: str) -> str: ...

    def violations_for(self, request_id: str) -> tuple[BoundaryViolation, ...]: ...

    async def close(self) -> None: ...


class ManagedNetworkProxy:
    """Small session-scoped HTTP CONNECT proxy with structured denials."""

    def __init__(
        self,
        *,
        policy: NetworkPolicy,
        server: _ListeningServer,
    ) -> None:
        self._policy = policy
        self._server = server
        self._violations: dict[str, list[BoundaryViolation]] = {}

    @classmethod
    async def open(cls, policy: NetworkPolicy) -> ManagedNetworkProxy:
        if not policy.enabled:
            raise ValueError("managed network proxy requires an enabled network policy")
        holder: dict[str, ManagedNetworkProxy] = {}

        async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await holder["proxy"]._handle_client(reader, writer)

        server = cast("_ListeningServer", await asyncio.start_server(_handle, "127.0.0.1", 0))
        proxy = cls(policy=policy, server=server)
        holder["proxy"] = proxy
        return proxy

    @property
    def port(self) -> int:
        sockets = self._server.sockets
        if not sockets:
            raise RuntimeError("managed network proxy is not listening")
        return int(sockets[0].getsockname()[1])

    def url_for(self, request_id: str) -> str:
        if not request_id or any(character in request_id for character in ":/@"):
            raise ValueError("network request id contains URL credential delimiters")
        return f"http://{request_id}:x@127.0.0.1:{self.port}"

    def violations_for(self, request_id: str) -> tuple[BoundaryViolation, ...]:
        return tuple(self._violations.pop(request_id, ()))

    async def close(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            if len(header) > _MAX_PROXY_HEADER_BYTES:
                await _write_proxy_response(writer, 431, "Request Header Fields Too Large")
                return
            request_line, headers = _parse_proxy_header(header)
            request_id = _proxy_request_id(headers)
            method, target, version = request_line.split(" ", 2)
            is_connect = method.upper() == "CONNECT"
            if is_connect:
                host, port = _parse_authority(target)
                origin_target = ""
            else:
                host, port, origin_target = _parse_http_target(target)
                if not _http_host_matches(headers.get("host"), host=host, port=port):
                    self._record(
                        request_id,
                        BoundaryViolation(
                            dimension="network.http_host",
                            requested=headers.get("host", "<missing>"),
                            evidence=(
                                "HTTP Host header does not match the policy-checked absolute URL"
                            ),
                            hard_deny=True,
                        ),
                    )
                    await _write_proxy_response(writer, 403, "Forbidden")
                    return
            domain_denial = evaluate_network_target(
                NetworkTarget(host=host, port=port, addresses=()),
                self._policy,
            )
            if domain_denial is not None:
                self._record(request_id, domain_denial)
                await _write_proxy_response(writer, 403, "Forbidden")
                return
            try:
                address = await self._resolve_allowed_address(host, port, request_id)
            except _PolicyDenied:
                await _write_proxy_response(writer, 403, "Forbidden")
                return
            except (OSError, socket.gaierror):
                await _write_proxy_response(writer, 502, "Bad Gateway")
                return
            initial_client_data = b""
            connect_established = False
            if is_connect and ipaddress.ip_address(address).is_global:
                await _write_proxy_response(writer, 200, "Connection Established")
                connect_established = True
                try:
                    initial_client_data = await _read_tls_client_hello(reader)
                    server_name = _tls_client_hello_server_name(initial_client_data)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError):
                    self._record(
                        request_id,
                        BoundaryViolation(
                            dimension="network.tls_server_name",
                            requested="<missing-or-invalid>",
                            evidence=(
                                "public CONNECT tunnel did not begin with a verifiable TLS "
                                "ClientHello"
                            ),
                            hard_deny=True,
                        ),
                    )
                    return
                if _normalized_domain(server_name) != _normalized_domain(host):
                    self._record(
                        request_id,
                        BoundaryViolation(
                            dimension="network.tls_server_name",
                            requested=server_name,
                            evidence="TLS server name does not match the policy-checked CONNECT host",
                            hard_deny=True,
                        ),
                    )
                    return
            try:
                upstream_reader, upstream_writer = await asyncio.open_connection(address, port)
            except OSError:
                if not connect_established:
                    await _write_proxy_response(writer, 502, "Bad Gateway")
                return
            try:
                if is_connect:
                    if not connect_established:
                        await _write_proxy_response(writer, 200, "Connection Established")
                    if initial_client_data:
                        upstream_writer.write(initial_client_data)
                        await upstream_writer.drain()
                else:
                    upstream_writer.write(
                        _origin_request_header(
                            method=method,
                            target=origin_target,
                            version=version,
                            headers=headers,
                        )
                    )
                    await upstream_writer.drain()
                await _relay_bidirectional(reader, writer, upstream_reader, upstream_writer)
            finally:
                upstream_writer.close()
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, UnicodeError, ValueError):
            with contextlib.suppress(Exception):
                await _write_proxy_response(writer, 400, "Bad Request")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _resolve_allowed_address(
        self,
        host: str,
        port: int,
        request_id: str,
    ) -> str:
        loop = asyncio.get_running_loop()
        results = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = tuple(sorted({str(result[4][0]) for result in results}))
        denial = evaluate_network_target(
            NetworkTarget(host=host, port=port, addresses=addresses),
            self._policy,
        )
        if denial is not None:
            self._record(request_id, denial)
            raise _PolicyDenied
        if not addresses:
            raise socket.gaierror("DNS returned no addresses")
        return addresses[0]

    def _record(self, request_id: str, violation: BoundaryViolation) -> None:
        self._violations.setdefault(request_id, []).append(violation)


class _PolicyDenied(Exception):
    pass


def _parse_proxy_header(header: bytes) -> tuple[str, dict[str, str]]:
    lines = header.decode("iso-8859-1").split("\r\n")
    if not lines or len(lines[0].split(" ", 2)) != 3:
        raise ValueError("invalid proxy request line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise ValueError("invalid proxy header")
        headers[name.strip().lower()] = value.strip()
    return lines[0], headers


def _proxy_request_id(headers: dict[str, str]) -> str:
    value = headers.get("proxy-authorization", "")
    scheme, separator, encoded = value.partition(" ")
    if not separator or scheme.lower() != "basic":
        return "unattributed"
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        return "unattributed"
    request_id, _, _ = decoded.partition(":")
    return request_id or "unattributed"


def _parse_authority(authority: str) -> tuple[str, int]:
    if authority.startswith("["):
        host, separator, port_text = authority[1:].partition("]:")
    else:
        host, separator, port_text = authority.rpartition(":")
    if not separator or not host:
        raise ValueError("CONNECT target must include host and port")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("CONNECT target port is out of range")
    return host, port


def _parse_http_target(target: str) -> tuple[str, int, str]:
    parsed = urlsplit(target)
    if parsed.scheme.lower() != "http" or parsed.hostname is None:
        raise ValueError("plain proxy request must use an absolute http URL")
    port = parsed.port or 80
    origin_target = parsed.path or "/"
    if parsed.query:
        origin_target = f"{origin_target}?{parsed.query}"
    return parsed.hostname, port, origin_target


def _http_host_matches(value: str | None, *, host: str, port: int) -> bool:
    if value is None:
        return False
    try:
        parsed = urlsplit(f"//{value}")
        header_host = parsed.hostname
        header_port = parsed.port or 80
    except ValueError:
        return False
    return (
        header_host is not None
        and _normalized_domain(header_host) == _normalized_domain(host)
        and header_port == port
    )


async def _read_tls_client_hello(reader: asyncio.StreamReader) -> bytes:
    header = await asyncio.wait_for(reader.readexactly(5), timeout=10.0)
    record_size = int.from_bytes(header[3:5], "big")
    if header[0] != 22 or record_size <= 0 or record_size > _MAX_TLS_CLIENT_HELLO_BYTES:
        raise ValueError("public CONNECT requires a bounded TLS ClientHello")
    payload = await asyncio.wait_for(reader.readexactly(record_size), timeout=10.0)
    return header + payload


def _tls_client_hello_server_name(record: bytes) -> str:
    """Extract the cleartext SNI from one bounded TLS ClientHello record."""
    if len(record) < 9 or record[0] != 22:
        raise ValueError("invalid TLS ClientHello record")
    record_size = int.from_bytes(record[3:5], "big")
    if record_size != len(record) - 5 or record[5] != 1:
        raise ValueError("invalid TLS ClientHello record")
    hello_size = int.from_bytes(record[6:9], "big")
    if hello_size + 9 > len(record):
        raise ValueError("truncated TLS ClientHello")
    offset = 9 + 2 + 32

    def _take_size(width: int) -> int:
        nonlocal offset
        if offset + width > len(record):
            raise ValueError("truncated TLS ClientHello")
        size = int.from_bytes(record[offset : offset + width], "big")
        offset += width
        return size

    session_size = _take_size(1)
    offset += session_size
    cipher_size = _take_size(2)
    offset += cipher_size
    compression_size = _take_size(1)
    offset += compression_size
    extensions_size = _take_size(2)
    extensions_end = offset + extensions_size
    if extensions_end > len(record):
        raise ValueError("truncated TLS ClientHello extensions")
    while offset + 4 <= extensions_end:
        extension_type = _take_size(2)
        extension_size = _take_size(2)
        extension_end = offset + extension_size
        if extension_end > extensions_end:
            raise ValueError("truncated TLS ClientHello extension")
        if extension_type == 0:
            names_size = _take_size(2)
            names_end = offset + names_size
            if names_end > extension_end:
                raise ValueError("truncated TLS server-name extension")
            while offset + 3 <= names_end:
                name_type = _take_size(1)
                name_size = _take_size(2)
                name_end = offset + name_size
                if name_end > names_end:
                    raise ValueError("truncated TLS server name")
                raw_name = record[offset:name_end]
                offset = name_end
                if name_type == 0:
                    try:
                        return _normalized_domain(raw_name.decode("ascii"))
                    except (UnicodeError, ValueError) as exc:
                        raise ValueError("invalid TLS server name") from exc
            raise ValueError("TLS ClientHello has no host_name entry")
        offset = extension_end
    raise ValueError("TLS ClientHello has no server-name extension")


def _origin_request_header(
    *,
    method: str,
    target: str,
    version: str,
    headers: dict[str, str],
) -> bytes:
    lines = [f"{method} {target} {version}"]
    for name, value in headers.items():
        if name in {"proxy-authorization", "proxy-connection"}:
            continue
        lines.append(f"{name}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")


async def _write_proxy_response(
    writer: asyncio.StreamWriter,
    status: int,
    reason: str,
) -> None:
    writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n\r\n".encode("ascii"))
    await writer.drain()


async def _relay_bidirectional(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
) -> None:
    async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()

    tasks = {
        asyncio.create_task(_copy(client_reader, upstream_writer)),
        asyncio.create_task(_copy(upstream_reader, client_writer)),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*done, *pending, return_exceptions=True)


__all__ = [
    "ManagedNetworkProxy",
    "NetworkProxySession",
    "NetworkTarget",
    "evaluate_network_target",
]
