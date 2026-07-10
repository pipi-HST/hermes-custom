"""Deterministic SSRF and response-size guards for extractor downloads."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx


REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5


class UnsafeURL(ValueError):
    pass


class DownloadTooLarge(ValueError):
    pass


def validate_public_http_url(url: str) -> str:
    value = (url or "").strip()
    try:
        parsed = urlparse(value)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("invalid URL") from exc
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURL("only http and https URLs are allowed")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeURL("URL host is invalid")

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeURL("local network targets are not allowed")
    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        direct_ip = None
    if direct_ip is not None:
        if not direct_ip.is_global:
            raise UnsafeURL("local or private network targets are not allowed")
        return value
    try:
        addresses = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise UnsafeURL("URL host could not be resolved") from exc
    if not addresses:
        raise UnsafeURL("URL host could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UnsafeURL("local or private network targets are not allowed")
    return value


def fetch_public_bytes(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> tuple[str, httpx.Headers, bytes]:
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    current_url = url
    try:
        for _redirect in range(MAX_REDIRECTS + 1):
            current_url = validate_public_http_url(current_url)
            with active_client.stream(
                "GET",
                current_url,
                headers=headers,
                follow_redirects=False,
            ) as response:
                _validate_connected_peer(response)
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_length = _content_length(response.headers)
                if content_length is not None and content_length > max_bytes:
                    raise DownloadTooLarge(f"response exceeds {max_bytes} bytes")
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise DownloadTooLarge(f"response exceeds {max_bytes} bytes")
                    chunks.append(chunk)
                return str(response.url), response.headers, b"".join(chunks)
        raise UnsafeURL("too many redirects")
    finally:
        if owns_client:
            active_client.close()


def decode_http_text(headers: httpx.Headers, content: bytes) -> str:
    content_type = headers.get("content-type", "")
    match = re.search(r"charset=([^;\s]+)", content_type, flags=re.IGNORECASE)
    encoding = match.group(1).strip("\"'") if match else "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def _content_length(headers: httpx.Headers) -> int | None:
    value = headers.get("content-length")
    if not value:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


def _validate_connected_peer(response: httpx.Response) -> None:
    network_stream = response.extensions.get("network_stream")
    get_extra_info = getattr(network_stream, "get_extra_info", None)
    if get_extra_info is None:
        return
    peer = get_extra_info("server_addr")
    if not peer:
        return
    try:
        peer_ip = ipaddress.ip_address(peer[0])
    except (ValueError, TypeError, IndexError) as exc:
        raise UnsafeURL("connected peer address is invalid") from exc
    if not peer_ip.is_global:
        raise UnsafeURL("connected peer is local or private")
