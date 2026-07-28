"""Fail-closed HTTPS fetching with SSRF, robots, and byte-budget controls."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import json
import re
import socket
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from trustworthy_kb.config import FetchSettings
from trustworthy_kb.governance.contracts import (
    FetchedEvidenceBlock,
    FetchedEvidenceDocument,
)
from trustworthy_kb.governance.errors import EvidenceFetchError, UnsafeFetchTargetError
from trustworthy_kb.governance.fingerprints import canonical_json_hash
from trustworthy_kb.governance.snapshot_store import EvidenceSnapshotStore

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ROBOTS_MAX_BYTES = 256 * 1024
_SAFE_FRESHNESS_HEADERS = (
    "cache-control",
    "content-length",
    "content-type",
    "date",
    "etag",
    "last-modified",
)


class AddressResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> frozenset[str]:
        """Resolve all candidate peer addresses."""


class SystemAddressResolver:
    """Resolve DNS through the event loop without caching across requests."""

    async def resolve(self, hostname: str, port: int) -> frozenset[str]:
        loop = asyncio.get_running_loop()
        try:
            records = await loop.getaddrinfo(
                hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        except OSError:
            raise EvidenceFetchError("evidence target DNS resolution failed") from None
        return frozenset(str(record[4][0]).split("%")[0] for record in records)


@dataclass(frozen=True, slots=True)
class _Download:
    status_code: int
    headers: httpx.Headers
    raw: bytes


class SecureWebFetcher:
    """Fetch public evidence without cookies, proxies, JavaScript, or implicit redirects."""

    def __init__(
        self,
        settings: FetchSettings,
        snapshot_store: EvidenceSnapshotStore,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: AddressResolver | None = None,
    ) -> None:
        self._settings = settings
        self._store = snapshot_store
        self._resolver = resolver or SystemAddressResolver()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=settings.timeout_seconds,
            trust_env=False,
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": ", ".join(settings.allowed_media_types),
            },
        )
        self._robots: dict[str, RobotFileParser | None] = {}

    async def aclose(self) -> None:
        """Close the internally owned HTTP client."""

        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, url: str) -> FetchedEvidenceDocument:
        """Validate, fetch, snapshot, and structurally extract one candidate URL."""

        original = normalize_public_https_url(url)
        current = original
        for redirect_count in range(self._settings.max_redirects + 1):
            if not await self._robots_allowed(current):
                raise UnsafeFetchTargetError("evidence target is disallowed by robots policy")
            download = await self._download_once(
                current,
                max_bytes=self._settings.max_raw_bytes,
                allowed_media_types=self._settings.allowed_media_types,
            )
            if download.status_code in _REDIRECT_STATUSES:
                if redirect_count >= self._settings.max_redirects:
                    raise UnsafeFetchTargetError("evidence redirect limit exceeded")
                location = download.headers.get("location")
                if not location:
                    raise UnsafeFetchTargetError("evidence redirect is missing a location")
                current = normalize_public_https_url(urljoin(current, location))
                continue
            if not 200 <= download.status_code < 300:
                raise EvidenceFetchError("evidence target returned a non-success status")
            return self._document(original, current, download)
        raise UnsafeFetchTargetError("evidence redirect limit exceeded")

    async def _robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            robots_url = f"{origin}/robots.txt"
            download = await self._download_once(
                robots_url,
                max_bytes=_ROBOTS_MAX_BYTES,
                allowed_media_types=("text/plain", "text/html"),
            )
            if download.status_code == 404:
                self._robots[origin] = None
            elif download.status_code in {401, 403}:
                parser = RobotFileParser()
                parser.parse(["User-agent: *", "Disallow: /"])
                self._robots[origin] = parser
            elif 200 <= download.status_code < 300:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(_decode_text(download.raw, download.headers).splitlines())
                self._robots[origin] = parser
            else:
                raise EvidenceFetchError("robots policy could not be verified")
        cached_parser = self._robots[origin]
        return cached_parser is None or cached_parser.can_fetch(self._settings.user_agent, url)

    async def _download_once(
        self,
        url: str,
        *,
        max_bytes: int,
        allowed_media_types: Iterable[str],
    ) -> _Download:
        parts = urlsplit(url)
        hostname = parts.hostname
        if hostname is None:
            raise UnsafeFetchTargetError("evidence target hostname is invalid")
        resolved = await self._resolver.resolve(hostname, parts.port or 443)
        if not resolved or any(not _is_public_ip(address) for address in resolved):
            raise UnsafeFetchTargetError("evidence target resolved to a non-public address")
        request = self._client.build_request("GET", url)
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException:
            raise EvidenceFetchError("evidence fetch timed out") from None
        except httpx.HTTPError:
            raise EvidenceFetchError("evidence fetch transport failed") from None
        try:
            peer = _response_peer(response)
            if peer not in resolved or not _is_public_ip(peer):
                raise UnsafeFetchTargetError("evidence peer address failed verification")
            if response.status_code not in _REDIRECT_STATUSES and response.status_code != 404:
                media_type = _media_type(response.headers)
                if media_type not in allowed_media_types:
                    raise UnsafeFetchTargetError("evidence response media type is not allowed")
            declared = response.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise EvidenceFetchError("evidence response exceeds the byte limit")
                except ValueError:
                    raise EvidenceFetchError(
                        "evidence response content length is invalid"
                    ) from None
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise EvidenceFetchError("evidence response exceeds the byte limit")
            return _Download(response.status_code, response.headers, bytes(body))
        finally:
            await response.aclose()

    def _document(
        self, original_url: str, final_url: str, download: _Download
    ) -> FetchedEvidenceDocument:
        media_type = _media_type(download.headers)
        decoded = _decode_content(
            download.raw,
            download.headers.get("content-encoding", "identity"),
            self._settings.max_decoded_bytes,
        )
        normalized_text, signals = _extract_text(
            decoded, media_type, download.headers, final_url=final_url
        )
        raw_hash, raw_ref = self._store.put_bytes("raw", download.raw, suffix="bin")
        blocks = _text_blocks(normalized_text)
        _extracted_hash, extracted_ref = self._store.put_json(
            "extracted",
            {
                "final_url": final_url,
                "media_type": media_type,
                "normalized_text": normalized_text,
                "blocks": [block.model_dump(mode="json") for block in blocks],
                "safety_signals": list(signals),
            },
        )
        freshness = {
            name: hashlib.sha256(download.headers[name].encode()).hexdigest()
            for name in _SAFE_FRESHNESS_HEADERS
            if name in download.headers
        }
        return FetchedEvidenceDocument(
            normalized_url=original_url,
            final_url=final_url,
            raw_content_hash=raw_hash,
            normalized_text_hash=hashlib.sha256(normalized_text.encode()).hexdigest(),
            media_type=media_type,
            byte_size=len(download.raw),
            captured_at=datetime_now_utc(),
            freshness_metadata_hash=canonical_json_hash(freshness),
            complete=bool(normalized_text),
            extraction_status="EXTRACTED" if normalized_text else "EMPTY",
            raw_snapshot_ref=raw_ref,
            extracted_snapshot_ref=extracted_ref,
            blocks=blocks,
            safety_signals=signals,
        )


def normalize_public_https_url(value: str) -> str:
    """Normalize an absolute HTTPS URL and reject credential or port smuggling."""

    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        raise UnsafeFetchTargetError("evidence target URL is invalid") from None
    if parts.scheme.lower() != "https" or not parts.netloc or parts.hostname is None:
        raise UnsafeFetchTargetError("evidence target must be an absolute HTTPS URL")
    if parts.username is not None or parts.password is not None:
        raise UnsafeFetchTargetError("evidence target must not contain credentials")
    if port not in {None, 443}:
        raise UnsafeFetchTargetError("evidence target port is not allowed")
    hostname = parts.hostname.rstrip(".").lower()
    if (
        not hostname
        or hostname == "localhost"
        or hostname.endswith((".local", ".localhost", ".internal"))
    ):
        raise UnsafeFetchTargetError("evidence target hostname is not public")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise UnsafeFetchTargetError("evidence target hostname is invalid") from None
    netloc = ascii_hostname if port is None else f"{ascii_hostname}:{port}"
    if ":" in ascii_hostname and not ascii_hostname.startswith("["):
        netloc = f"[{ascii_hostname}]" if port is None else f"[{ascii_hostname}]:{port}"
    path = parts.path or "/"
    return urlunsplit(("https", netloc, path, parts.query, ""))


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global


def _response_peer(response: httpx.Response) -> str:
    stream = response.extensions.get("network_stream")
    getter = getattr(stream, "get_extra_info", None)
    if not callable(getter):
        raise UnsafeFetchTargetError("evidence peer address is unavailable")
    peer = getter("server_addr") or getter("peername")
    if isinstance(peer, (tuple, list)) and peer:
        return str(peer[0]).split("%")[0]
    if isinstance(peer, str):
        return peer.split("%")[0]
    raise UnsafeFetchTargetError("evidence peer address is unavailable")


def _media_type(headers: httpx.Headers) -> str:
    content_type = str(headers.get("content-type", ""))
    return content_type.split(";", 1)[0].strip().lower()


def _decode_content(raw: bytes, encoding: str, limit: int) -> bytes:
    normalized = encoding.strip().lower()
    try:
        if normalized in {"", "identity"}:
            decoded = raw
        elif normalized in {"gzip", "x-gzip"}:
            from io import BytesIO

            with gzip.GzipFile(fileobj=BytesIO(raw)) as compressed:
                decoded = compressed.read(limit + 1)
        elif normalized == "deflate":
            decompressor = zlib.decompressobj()
            decoded = decompressor.decompress(raw, limit + 1)
            if decompressor.unconsumed_tail:
                decoded += b"x"
        else:
            raise UnsafeFetchTargetError("evidence content encoding is not allowed")
    except (OSError, EOFError, zlib.error):
        raise EvidenceFetchError("evidence response decompression failed") from None
    if len(decoded) > limit:
        raise EvidenceFetchError("decoded evidence exceeds the byte limit")
    return decoded


def _decode_text(raw: bytes, headers: httpx.Headers) -> str:
    decoded = _decode_content(raw, headers.get("content-encoding", "identity"), _ROBOTS_MAX_BYTES)
    content_type = headers.get("content-type", "")
    match = re.search(r"charset=([\w.-]+)", content_type, flags=re.IGNORECASE)
    charset = match.group(1) if match else "utf-8"
    try:
        return decoded.decode(charset, errors="replace")
    except LookupError:
        return decoded.decode("utf-8", errors="replace")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.canonical_url: str | None = None
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self._hidden_depth += 1
        if tag == "link":
            values = {key.lower(): value for key, value in attrs}
            if (values.get("rel") or "").lower() == "canonical":
                self.canonical_url = values.get("href")
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


def _extract_text(
    decoded: bytes,
    media_type: str,
    headers: httpx.Headers,
    *,
    final_url: str,
) -> tuple[str, tuple[str, ...]]:
    if media_type in {"text/plain", "application/json"}:
        text = _decode_text(
            decoded, httpx.Headers({"content-type": headers.get("content-type", "")})
        )
        if media_type == "application/json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True)
            except json.JSONDecodeError:
                return "", ("invalid_json",)
        return _normalize_text(text), ()
    if media_type == "text/html":
        parser = _VisibleTextParser()
        parser.feed(
            _decode_text(decoded, httpx.Headers({"content-type": headers.get("content-type", "")}))
        )
        signals: tuple[str, ...] = ()
        if parser.canonical_url:
            try:
                canonical = normalize_public_https_url(urljoin(final_url, parser.canonical_url))
                if urlsplit(canonical).hostname != urlsplit(final_url).hostname:
                    signals = ("canonical_origin_mismatch",)
            except UnsafeFetchTargetError:
                signals = ("canonical_link_invalid",)
        return _normalize_text("".join(parser.parts)), signals
    return "", ("unsupported_extraction",)


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _text_blocks(text: str) -> tuple[FetchedEvidenceBlock, ...]:
    if not text:
        return ()
    chunks: list[str] = []
    for paragraph in text.split("\n"):
        for offset in range(0, len(paragraph), 2000):
            chunk = paragraph[offset : offset + 2000].strip()
            if chunk:
                chunks.append(chunk)
            if len(chunks) >= 128:
                break
        if len(chunks) >= 128:
            break
    return tuple(
        FetchedEvidenceBlock(
            anchor=f"text:block:{position}",
            text=chunk,
            text_hash=hashlib.sha256(chunk.encode()).hexdigest(),
        )
        for position, chunk in enumerate(chunks)
    )


def datetime_now_utc() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "AddressResolver",
    "SecureWebFetcher",
    "SystemAddressResolver",
    "normalize_public_https_url",
]
