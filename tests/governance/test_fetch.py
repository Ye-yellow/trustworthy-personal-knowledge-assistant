from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

import httpx
import pytest

from trustworthy_kb.config import FetchSettings
from trustworthy_kb.governance.errors import EvidenceFetchError, UnsafeFetchTargetError
from trustworthy_kb.governance.fetch import SecureWebFetcher, normalize_public_https_url
from trustworthy_kb.governance.snapshot_store import EvidenceSnapshotStore

PUBLIC_IP = "93.184.216.34"


class FakeResolver:
    def __init__(self, addresses: dict[str, frozenset[str]] | None = None) -> None:
        self.addresses = addresses or {"example.com": frozenset({PUBLIC_IP})}
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> frozenset[str]:
        self.calls.append((hostname, port))
        return self.addresses.get(hostname, frozenset())


class FakeStream:
    def __init__(self, peer: str = PUBLIC_IP) -> None:
        self.peer = peer

    def get_extra_info(self, name: str) -> Any:
        if name == "server_addr":
            return (self.peer, 443)
        return None


def response(
    status: int,
    *,
    content: bytes = b"",
    headers: dict[str, str] | None = None,
    peer: str = PUBLIC_IP,
) -> httpx.Response:
    return httpx.Response(
        status,
        stream=httpx.ByteStream(content),
        headers=headers,
        extensions={"network_stream": FakeStream(peer)},
    )


def fetcher(
    tmp_path: Path,
    handler: Any,
    *,
    resolver: FakeResolver | None = None,
    settings: FetchSettings | None = None,
) -> SecureWebFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return SecureWebFetcher(
        settings or FetchSettings(),
        EvidenceSnapshotStore(tmp_path / "evidence"),
        client=client,
        resolver=resolver or FakeResolver(),
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/page",
        "https://user:password@example.com/page",
        "https://example.com:444/page",
        "https://localhost/page",
        "relative/path",
    ],
)
def test_url_policy_rejects_non_public_shapes(url: str) -> None:
    with pytest.raises(UnsafeFetchTargetError):
        normalize_public_https_url(url)


def test_url_policy_normalizes_case_fragment_and_default_path() -> None:
    assert (
        normalize_public_https_url(" HTTPS://Example.COM#private-fragment ")
        == "https://example.com/"
    )


@pytest.mark.asyncio
async def test_fetcher_checks_robots_peer_extracts_and_snapshots_html(tmp_path: Path) -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return response(404)
        return response(
            200,
            content=(
                b"<html><head><link rel='canonical' href='https://example.com/page'></head>"
                b"<body><h1>Synthetic heading</h1><script>secret()</script>"
                b"<p>Verified public text.</p></body></html>"
            ),
            headers={"Content-Type": "text/html; charset=utf-8", "ETag": "synthetic"},
        )

    web_fetcher = fetcher(tmp_path, handler)
    document = await web_fetcher.fetch("https://example.com/page#fragment")

    assert requested == ["https://example.com/robots.txt", "https://example.com/page"]
    assert str(document.final_url) == "https://example.com/page"
    assert document.complete
    assert "secret" not in " ".join(block.text for block in document.blocks)
    assert "Verified public text." in " ".join(block.text for block in document.blocks)
    assert document.safety_signals == ()
    store = EvidenceSnapshotStore(tmp_path / "evidence")
    assert store.load_bytes(document.raw_snapshot_ref, document.raw_content_hash).startswith(
        b"<html>"
    )


@pytest.mark.asyncio
async def test_fetcher_flags_cross_origin_canonical_identity(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return response(404)
        return response(
            200,
            content=(
                b"<html><head><link rel='canonical' href='https://other.example/page'>"
                b"</head><body>Evidence text.</body></html>"
            ),
            headers={"Content-Type": "text/html"},
        )

    document = await fetcher(tmp_path, handler).fetch("https://example.com/page")

    assert document.safety_signals == ("canonical_origin_mismatch",)


@pytest.mark.asyncio
async def test_fetcher_honors_robots_disallow_without_requesting_page(tmp_path: Path) -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return response(
            200,
            content=b"User-agent: *\nDisallow: /private",
            headers={"Content-Type": "text/plain"},
        )

    web_fetcher = fetcher(tmp_path, handler)

    with pytest.raises(UnsafeFetchTargetError, match="robots"):
        await web_fetcher.fetch("https://example.com/private/page")
    assert requested == ["https://example.com/robots.txt"]


@pytest.mark.asyncio
async def test_fetcher_rejects_private_dns_before_network_request(tmp_path: Path) -> None:
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return response(200, headers={"Content-Type": "text/plain"})

    resolver = FakeResolver({"example.com": frozenset({"127.0.0.1"})})
    web_fetcher = fetcher(tmp_path, handler, resolver=resolver)

    with pytest.raises(UnsafeFetchTargetError, match="non-public"):
        await web_fetcher.fetch("https://example.com/page")
    assert requests == 0


@pytest.mark.asyncio
async def test_fetcher_revalidates_redirect_and_blocks_private_target(tmp_path: Path) -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return response(404)
        return response(302, headers={"Location": "https://private.example/secret"})

    resolver = FakeResolver(
        {
            "example.com": frozenset({PUBLIC_IP}),
            "private.example": frozenset({"10.0.0.5"}),
        }
    )
    web_fetcher = fetcher(tmp_path, handler, resolver=resolver)

    with pytest.raises(UnsafeFetchTargetError, match="non-public"):
        await web_fetcher.fetch("https://example.com/start")
    assert requested == ["https://example.com/robots.txt", "https://example.com/start"]


@pytest.mark.asyncio
async def test_fetcher_rejects_dns_peer_mismatch(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return response(404)
        return response(
            200,
            content=b"public",
            headers={"Content-Type": "text/plain"},
            peer="93.184.216.35",
        )

    web_fetcher = fetcher(tmp_path, handler)

    with pytest.raises(UnsafeFetchTargetError, match="peer address"):
        await web_fetcher.fetch("https://example.com/page")


@pytest.mark.asyncio
async def test_fetcher_enforces_raw_and_decoded_byte_budgets(tmp_path: Path) -> None:
    large = b"x" * 2048
    compressed = gzip.compress(large)

    async def raw_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return response(404)
        return response(200, content=large, headers={"Content-Type": "text/plain"})

    raw_fetcher = fetcher(tmp_path, raw_handler, settings=FetchSettings(max_raw_bytes=1024))
    with pytest.raises(EvidenceFetchError, match="byte limit"):
        await raw_fetcher.fetch("https://example.com/raw")

    async def compressed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return response(404)
        return response(
            200,
            content=compressed,
            headers={"Content-Type": "text/plain", "Content-Encoding": "gzip"},
        )

    decoded_fetcher = fetcher(
        tmp_path,
        compressed_handler,
        settings=FetchSettings(max_decoded_bytes=1024),
    )
    with pytest.raises(EvidenceFetchError, match="decoded evidence"):
        await decoded_fetcher.fetch("https://example.com/compressed")
