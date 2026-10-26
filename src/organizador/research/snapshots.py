"""Research cycles and evidence snapshots.

A research cycle is one manual run for one project:

    <root>/research/<cycle_id>/cycle.json
    <root>/research/<cycle_id>/snapshots/<snap_id>.json

A snapshot is the readable text of one page that research explicitly chose to
use as evidence, with its original URL, final URL after redirects, retrieval
time and the SHA-256 of the bytes received. Raw HTML is not kept, search
results are never stored wholesale, and a cycle holds at most
MAX_SNAPSHOTS_PER_CYCLE snapshots. Snapshots are created once and never
rewritten.

Fetching is a single read-only HTTPS GET with manual redirects. Every hop
must be HTTPS on port 443 to a host whose addresses are all public; the
connection goes to the address that was checked, so a second DNS answer
cannot redirect it to a private network.

A quote found in a snapshot shows where a statement comes from. It does not
prove that our interpretation of it is right.
"""

import hashlib
import http.client
import ipaddress
import re
import secrets
import socket
import ssl
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from organizador.recommendations import common
from organizador.recommendations.common import SCHEMA_VERSION, ConflictError, DataError, NotFoundError

RESEARCH_DIR = "research"
SNAPSHOTS_DIR = "snapshots"
CYCLE_ID_RE = re.compile(r"cyc-\d{8}T\d{6}Z-[0-9a-f]{8}", re.ASCII)
SNAPSHOT_ID_RE = re.compile(r"snap-[0-9a-f]{16}", re.ASCII)

MAX_SNAPSHOTS_PER_CYCLE = 20
MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 200_000
MAX_REDIRECTS = 5
MIN_QUOTE_CHARS = 20
TIMEOUT_SECONDS = 20
USER_AGENT = "organizador-research/0.1 (personal, read-only)"
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown")
REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# resolver(host, port) -> IP address strings
Resolver = Callable[[str, int], list[str]]
# transport(host, ip, port, target, headers) -> (status, headers, body)
Transport = Callable[[str, str, int, str, dict[str, str]], tuple[int, dict[str, str], bytes]]

_CYCLE_KEYS = ("schema_version", "id", "project_id", "started_at")
_SNAPSHOT_KEYS = (
    "schema_version",
    "id",
    "cycle_id",
    "url",
    "final_url",
    "retrieved_at",
    "http_status",
    "content_type",
    "content_sha256",
    "content_bytes",
    "text_sha256",
    "text_truncated",
    "text",
)


class FetchError(Exception):
    """The page could not be fetched safely or is not usable as text evidence."""


@dataclass(frozen=True)
class Snapshot:
    id: str
    cycle_id: str
    url: str
    final_url: str
    retrieved_at: str
    http_status: int
    content_type: str
    content_sha256: str
    content_bytes: int
    text_sha256: str
    text_truncated: bool
    text: str


# --- cycles ----------------------------------------------------------------


def start_cycle(root: Path, project_id: str, now: datetime | None = None) -> str:
    """Create a new, empty research cycle for `project_id` and return its ID."""
    try:
        common.project_id(project_id)
    except ValueError as e:
        raise DataError(str(e)) from None
    started_at = common.utc_now(now)
    cycle_id = f"cyc-{started_at.replace('-', '').replace(':', '')}-{secrets.token_hex(4)}"
    path = _cycle_dir(root, cycle_id) / "cycle.json"
    data = {"schema_version": SCHEMA_VERSION, "id": cycle_id, "project_id": project_id, "started_at": started_at}
    try:
        common.create_new_file(path, common.dump_json(data))
    except FileExistsError:
        raise ConflictError(f"Research cycle {cycle_id} already exists") from None
    return cycle_id


def load_cycle(root: Path, cycle_id: str) -> dict:
    path = _cycle_dir(root, cycle_id) / "cycle.json"
    _refuse_link(path)
    try:
        data = common.read_json_object(path)
    except FileNotFoundError:
        raise NotFoundError(f"Research cycle {cycle_id} not found ({path})") from None
    try:
        common.check_keys(data, _CYCLE_KEYS, ())
        common.check_schema_version(data)
        if data["id"] != cycle_id:
            raise ValueError(f"id {data['id']!r} does not match the folder name")
        common.project_id(data["project_id"])
        common.timestamp(data["started_at"], "started_at")
    except ValueError as e:
        raise DataError(f"Invalid research cycle {path}: {e}") from None
    return data


# --- snapshots -------------------------------------------------------------


def snapshot_id_for(url: str) -> str:
    return "snap-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def fetch_snapshot(
    root: Path,
    cycle_id: str,
    url: str,
    *,
    now: datetime | None = None,
    resolver: Resolver | None = None,
    transport: Transport | None = None,
) -> tuple[Snapshot, bool]:
    """Fetch `url` once for this cycle and store its text. Returns (snapshot, created).

    A URL already captured in the cycle is returned as stored, without a
    second request.
    """
    load_cycle(root, cycle_id)
    url = _without_fragment(url)
    check_url(url)
    folder = _snapshots_dir(root, cycle_id)
    snap_id = snapshot_id_for(url)
    path = folder / f"{snap_id}.json"
    _refuse_link(path)
    if path.exists():
        return load_snapshot(root, cycle_id, snap_id), False
    if folder.is_dir() and len(list(folder.glob("snap-*.json"))) >= MAX_SNAPSHOTS_PER_CYCLE:
        raise FetchError(f"This cycle already has {MAX_SNAPSHOTS_PER_CYCLE} snapshots, the maximum")

    page = fetch_page(url, resolver=resolver or default_resolver, transport=transport or default_transport)
    text, truncated = page_text(page["body"], page["content_type"], page["charset"])
    record = {
        "schema_version": SCHEMA_VERSION,
        "id": snap_id,
        "cycle_id": cycle_id,
        "url": url,
        "final_url": page["final_url"],
        "retrieved_at": common.utc_now(now),
        "http_status": page["status"],
        "content_type": page["content_type"],
        "content_sha256": hashlib.sha256(page["body"]).hexdigest(),
        "content_bytes": len(page["body"]),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_truncated": truncated,
        "text": text,
    }
    snapshot = _snapshot_from_dict(record, cycle_id, snap_id)
    try:
        common.create_new_file(path, common.dump_json(record))
    except FileExistsError:
        raise ConflictError(f"Snapshot {snap_id} was created concurrently; nothing was written") from None
    return snapshot, True


def load_snapshot(root: Path, cycle_id: str, snap_id: str) -> Snapshot:
    if not isinstance(snap_id, str) or not SNAPSHOT_ID_RE.fullmatch(snap_id):
        raise DataError(f"snapshot id {snap_id!r} is not valid")
    path = _snapshots_dir(root, cycle_id) / f"{snap_id}.json"
    _refuse_link(path)
    try:
        data = common.read_json_object(path)
    except FileNotFoundError:
        raise NotFoundError(f"Snapshot {snap_id} not found in cycle {cycle_id}") from None
    try:
        return _snapshot_from_dict(data, cycle_id, snap_id)
    except ValueError as e:
        raise DataError(f"Invalid snapshot {path}: {e}") from None


def list_snapshots(root: Path, cycle_id: str) -> tuple[Snapshot, ...]:
    folder = _snapshots_dir(root, cycle_id)
    if not folder.is_dir():
        return ()
    found = []
    for path in sorted(folder.iterdir()):
        snap_id = path.name[: -len(".json")] if path.name.endswith(".json") else ""
        if not SNAPSHOT_ID_RE.fullmatch(snap_id):
            raise DataError(f"Unexpected entry in {folder}: {path.name}")
        found.append(load_snapshot(root, cycle_id, snap_id))
    return tuple(found)


# --- quotes ----------------------------------------------------------------

_QUOTE_TRANSLATION = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
    }
)


def normalize_text(value: str) -> str:
    """Unicode NFKC, straight quotes and dashes, single spaces. Case is kept."""
    value = unicodedata.normalize("NFKC", value).translate(_QUOTE_TRANSLATION)
    return " ".join(value.split())


def quote_problem(snapshot: Snapshot, quote: str) -> str | None:
    """None if `quote` appears in the snapshot text, otherwise the reason.

    Matching ignores differences in whitespace, curly quotes and dashes, but
    not wording or case. A match makes the statement traceable to the page;
    it does not make our interpretation of it correct.
    """
    if not isinstance(quote, str):
        return "the quote must be text"
    normalized = normalize_text(quote)
    if len(normalized) < MIN_QUOTE_CHARS:
        return f"the quote is shorter than {MIN_QUOTE_CHARS} characters"
    if normalized not in normalize_text(snapshot.text):
        return f"the quote was not found in snapshot {snapshot.id} ({snapshot.url})"
    return None


# --- fetching --------------------------------------------------------------


def check_url(url: str):
    """Raise FetchError unless `url` is an absolute HTTPS URL on port 443 without credentials."""
    try:
        common.url(url, "url")
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise FetchError(str(e)) from None
    if parts.scheme != "https":
        raise FetchError(f"only https:// URLs can be fetched, got {url!r}")
    if port not in (None, 443):
        raise FetchError(f"only port 443 is allowed, got {port}")
    return parts


def public_addresses(host: str, port: int, resolver: Resolver) -> list[str]:
    """Addresses for `host`, all of them public; otherwise FetchError."""
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = resolver(host, port)
        except OSError as e:
            raise FetchError(f"cannot resolve {host}: {e}") from None
    if not addresses:
        raise FetchError(f"{host} has no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise FetchError(f"{host} resolves to {address}, which is not a public address")
    return addresses


def fetch_page(url: str, *, resolver: Resolver, transport: Transport) -> dict:
    """GET `url`, following up to MAX_REDIRECTS redirects, each checked like the first request."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parts = check_url(current)
        host, port = parts.hostname, parts.port or 443
        ip = public_addresses(host, port, resolver)[0]
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html, text/plain;q=0.9, */*;q=0.1"}
        status, response_headers, body = transport(host, ip, port, target, headers)
        response_headers = {k.lower(): v for k, v in response_headers.items()}

        if status in REDIRECT_STATUSES:
            location = response_headers.get("location")
            if not location:
                raise FetchError(f"redirect {status} from {current} has no Location")
            current = _without_fragment(urljoin(current, location))
            continue
        if status != 200:
            raise FetchError(f"HTTP {status} from {current}")
        if response_headers.get("content-encoding", "identity").lower() != "identity":
            raise FetchError(f"compressed responses are not supported ({response_headers['content-encoding']})")
        content_type, charset = _content_type(response_headers.get("content-type", ""))
        if content_type not in TEXT_TYPES:
            raise FetchError(f"{current} is {content_type or 'of unknown type'}, not readable text")
        return {"final_url": current, "status": status, "content_type": content_type, "charset": charset, "body": body}
    raise FetchError(f"more than {MAX_REDIRECTS} redirects starting at {url}")


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to an already checked IP address, with certificate checks for the host name."""

    def __init__(self, host: str, ip: str, port: int):
        super().__init__(host, port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def default_transport(host: str, ip: str, port: int, target: str, headers: dict[str, str]):
    connection = _PinnedHTTPSConnection(host, ip, port)
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise FetchError(f"the response from {host} is larger than {MAX_BYTES} bytes")
        return response.status, dict(response.getheaders()), body
    except (OSError, http.client.HTTPException) as e:
        raise FetchError(f"request to {host} failed: {e}") from None
    finally:
        connection.close()


# --- text extraction -------------------------------------------------------


class _TextExtractor(HTMLParser):
    """Visible text of an HTML page. Scripts, styles and similar are dropped."""

    SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "object"}
    BLOCK = {
        "address", "article", "aside", "blockquote", "br", "dd", "details", "div", "dl", "dt",
        "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
        "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "td", "th",
        "title", "tr", "ul",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0
        self.preformatted = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "pre":
            self.preformatted += 1

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "pre":
            self.preformatted = max(0, self.preformatted - 1)

    def handle_data(self, data):
        if self.skipping:
            return
        # Line breaks in the HTML source are only spaces, except inside <pre>.
        self.parts.append(data if self.preformatted else data.replace("\r", " ").replace("\n", " "))


def page_text(body: bytes, content_type: str, charset: str | None) -> tuple[str, bool]:
    """Readable text: one line per block, single spaces. Returns (text, truncated)."""
    try:
        decoded = body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        decoded = body.decode("utf-8", errors="replace")
    if content_type in ("text/html", "application/xhtml+xml"):
        extractor = _TextExtractor()
        extractor.feed(decoded)
        extractor.close()
        decoded = "".join(extractor.parts)
    lines = [" ".join(line.split()) for line in decoded.splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS], True
    return text, False


def _content_type(value: str) -> tuple[str, str | None]:
    main, *params = [p.strip() for p in value.split(";")]
    charset = None
    for param in params:
        key, _, val = param.partition("=")
        if key.strip().lower() == "charset":
            charset = val.strip().strip('"') or None
    return main.lower(), charset


# --- storage helpers -------------------------------------------------------


def _cycle_dir(root: Path, cycle_id: str) -> Path:
    if not isinstance(cycle_id, str) or not CYCLE_ID_RE.fullmatch(cycle_id):
        raise DataError(f"cycle id {cycle_id!r} is not valid")
    return common.record_path(root, RESEARCH_DIR, cycle_id, "")


def _snapshots_dir(root: Path, cycle_id: str) -> Path:
    folder = _cycle_dir(root, cycle_id) / SNAPSHOTS_DIR
    _refuse_link(folder)
    return folder


def _refuse_link(path: Path) -> None:
    """Paths here are built from a resolved data root; one that resolves elsewhere crosses a link."""
    if path.exists() or path.is_symlink():
        if path.resolve() != path:
            raise DataError(f"Unsafe path {path}: it is a link to another location")


def _without_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _snapshot_from_dict(data, cycle_id: str, snap_id: str) -> Snapshot:
    common.check_keys(data, _SNAPSHOT_KEYS, ())
    common.check_schema_version(data)
    if data["id"] != snap_id or data["cycle_id"] != cycle_id:
        raise ValueError("id or cycle_id does not match the file location")
    if snapshot_id_for(data["url"]) != snap_id:
        raise ValueError("id does not match url")
    common.url(data["url"], "url")
    common.url(data["final_url"], "final_url")
    common.timestamp(data["retrieved_at"], "retrieved_at")
    if data["http_status"] != 200 or isinstance(data["http_status"], bool):
        raise ValueError("http_status must be 200")
    if data["content_type"] not in TEXT_TYPES:
        raise ValueError(f"content_type {data['content_type']!r} is not a text type")
    for key in ("content_sha256", "text_sha256"):
        if not isinstance(data[key], str) or not re.fullmatch(r"[0-9a-f]{64}", data[key]):
            raise ValueError(f"{key} must be a SHA-256 hex digest")
    if not isinstance(data["content_bytes"], int) or isinstance(data["content_bytes"], bool) or data["content_bytes"] < 0:
        raise ValueError("content_bytes must be a non-negative integer")
    if not isinstance(data["text_truncated"], bool):
        raise ValueError("text_truncated must be true or false")
    if not isinstance(data["text"], str):
        raise ValueError("text must be a string")
    if hashlib.sha256(data["text"].encode("utf-8")).hexdigest() != data["text_sha256"]:
        raise ValueError("text does not match text_sha256 (the snapshot was changed)")
    return Snapshot(**{k: data[k] for k in _SNAPSHOT_KEYS if k != "schema_version"})
