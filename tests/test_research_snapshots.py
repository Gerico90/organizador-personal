import json
import os
from datetime import datetime, timezone

import pytest

from organizador.recommendations import common
from organizador.recommendations.common import DataError, NotFoundError
from organizador.research import snapshots as snap_module
from organizador.research.__main__ import main
from organizador.research.snapshots import (
    MAX_SNAPSHOTS_PER_CYCLE,
    FetchError,
    check_url,
    fetch_page,
    fetch_snapshot,
    list_snapshots,
    load_cycle,
    load_snapshot,
    page_text,
    public_addresses,
    quote_problem,
    snapshot_id_for,
    start_cycle,
)

NOW = datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc)
PUBLIC_IP = "93.184.216.34"

PAGE = b"""<!doctype html><html><head><title>Demo docs</title>
<style>.x { color: red }</style><script>var secret = "never shown in text";</script></head>
<body><nav>Home | Docs</nav>
<h1>Property&nbsp;testing</h1>
<p>The library <b>generates</b> random inputs and <em>shrinks</em> failing cases to a minimal example.</p>
<ul><li>First item</li><li>Second item</li></ul>
<noscript>enable javascript</noscript>
</body></html>"""


class FakeNet:
    """Resolver and transport for offline tests. `routes` maps (host, target) to a response."""

    def __init__(self, routes=None, addresses=None):
        self.routes = routes or {}
        self.addresses = addresses or {}
        self.calls = []

    def resolver(self, host, port):
        return self.addresses.get(host, [PUBLIC_IP])

    def transport(self, host, ip, port, target, headers):
        self.calls.append((host, ip, port, target))
        status, response_headers, body = self.routes[(host, target)]
        return status, response_headers, body


def html(body=PAGE, status=200, **headers):
    return status, {"Content-Type": "text/html; charset=utf-8", **headers}, body


def redirect(location, status=302):
    return status, {"Location": location}, b""


@pytest.fixture
def cycle(tmp_path):
    return start_cycle(tmp_path, "demo-project", now=NOW)


def fetch(tmp_path, cycle, url, net):
    return fetch_snapshot(tmp_path, cycle, url, now=NOW, resolver=net.resolver, transport=net.transport)


# --- URL and address checks ------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "https://example.com:8443/",
        "https://user:pw@example.com/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "https://",
        "not a url",
    ],
)
def test_check_url_rejects(url):
    with pytest.raises(FetchError):
        check_url(url)


def test_check_url_accepts_https():
    assert check_url("https://example.com:443/docs?a=1").hostname == "example.com"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_private_and_special_addresses_are_rejected(address):
    with pytest.raises(FetchError, match="not a public address"):
        public_addresses("internal.example", 443, lambda h, p: [address])


def test_ip_literal_hosts_are_checked_without_resolving():
    with pytest.raises(FetchError, match="not a public address"):
        public_addresses("127.0.0.1", 443, lambda h, p: pytest.fail("must not resolve"))
    assert public_addresses(PUBLIC_IP, 443, lambda h, p: pytest.fail("must not resolve")) == [PUBLIC_IP]


def test_one_private_address_among_public_ones_is_rejected():
    with pytest.raises(FetchError):
        public_addresses("mixed.example", 443, lambda h, p: [PUBLIC_IP, "10.0.0.5"])


def test_resolution_failures():
    def broken(host, port):
        raise OSError("no such host")

    with pytest.raises(FetchError, match="cannot resolve"):
        public_addresses("x.example", 443, broken)
    with pytest.raises(FetchError, match="no addresses"):
        public_addresses("x.example", 443, lambda h, p: [])


# --- fetching and redirects ------------------------------------------------


def test_fetch_connects_to_the_checked_address():
    net = FakeNet({("docs.example", "/guide?x=1"): html()})
    page = fetch_page("https://docs.example/guide?x=1", resolver=net.resolver, transport=net.transport)
    assert page["final_url"] == "https://docs.example/guide?x=1"
    assert net.calls == [("docs.example", PUBLIC_IP, 443, "/guide?x=1")]


def test_redirects_are_followed_and_checked():
    net = FakeNet(
        {
            ("a.example", "/"): redirect("/moved"),
            ("a.example", "/moved"): redirect("https://b.example/final#frag", 301),
            ("b.example", "/final"): html(),
        }
    )
    page = fetch_page("https://a.example/", resolver=net.resolver, transport=net.transport)
    assert page["final_url"] == "https://b.example/final"
    assert [c[0] for c in net.calls] == ["a.example", "a.example", "b.example"]


@pytest.mark.parametrize(
    "location, addresses, message",
    [
        ("http://b.example/", {}, "only https"),
        ("https://b.example:8080/", {}, "only port 443"),
        ("https://internal.example/", {"internal.example": ["10.0.0.7"]}, "not a public address"),
        ("https://169.254.169.254/latest/meta-data/", {}, "not a public address"),
        ("https://user:pw@b.example/", {}, "credentials"),
    ],
)
def test_unsafe_redirects_are_refused(location, addresses, message):
    net = FakeNet({("a.example", "/"): redirect(location)}, addresses)
    with pytest.raises(FetchError, match=message):
        fetch_page("https://a.example/", resolver=net.resolver, transport=net.transport)
    assert len(net.calls) == 1  # the unsafe target is never contacted


def test_redirect_limit_and_missing_location():
    loop = FakeNet({("a.example", "/"): redirect("/")})
    with pytest.raises(FetchError, match="more than 5 redirects"):
        fetch_page("https://a.example/", resolver=loop.resolver, transport=loop.transport)
    empty = FakeNet({("a.example", "/"): (302, {}, b"")})
    with pytest.raises(FetchError, match="no Location"):
        fetch_page("https://a.example/", resolver=empty.resolver, transport=empty.transport)


@pytest.mark.parametrize(
    "response, message",
    [
        ((404, {"Content-Type": "text/html"}, b"nope"), "HTTP 404"),
        ((200, {"Content-Type": "application/pdf"}, b"%PDF"), "not readable text"),
        ((200, {"Content-Type": "image/png"}, b"\x89PNG"), "not readable text"),
        ((200, {}, b"x"), "unknown type"),
        ((200, {"Content-Type": "text/html", "Content-Encoding": "gzip"}, b"x"), "compressed"),
    ],
)
def test_unusable_responses(response, message):
    net = FakeNet({("a.example", "/"): response})
    with pytest.raises(FetchError, match=message):
        fetch_page("https://a.example/", resolver=net.resolver, transport=net.transport)


# --- text extraction -------------------------------------------------------


def test_html_text_extraction():
    text, truncated = page_text(PAGE, "text/html", "utf-8")
    assert truncated is False
    assert text.splitlines() == [
        "Demo docs",
        "Home | Docs",
        "Property testing",
        "The library generates random inputs and shrinks failing cases to a minimal example.",
        "First item",
        "Second item",
    ]
    assert "secret" not in text and "color" not in text and "javascript" not in text


def test_source_line_breaks_inside_a_paragraph_are_spaces():
    body = b"<p>one paragraph\nwrapped in the\r\nsource</p><pre>line 1\nline 2</pre>"
    assert page_text(body, "text/html", "utf-8")[0].splitlines() == [
        "one paragraph wrapped in the source",
        "line 1",
        "line 2",
    ]


def test_plain_text_and_charsets():
    assert page_text(b"  a   b \n\n c ", "text/plain", None) == ("a b\nc", False)
    assert page_text("café".encode("latin-1"), "text/plain", "latin-1") == ("café", False)
    assert page_text("café".encode("utf-8"), "text/plain", "no-such-charset") == ("café", False)


def test_text_is_bounded(monkeypatch):
    monkeypatch.setattr(snap_module, "MAX_TEXT_CHARS", 10)
    assert page_text(b"0123456789abcdef", "text/plain", None) == ("0123456789", True)


# --- cycles and snapshots --------------------------------------------------


def test_start_and_load_cycle(tmp_path):
    cycle_id = start_cycle(tmp_path, "demo-project", now=NOW)
    assert cycle_id.startswith("cyc-20261027T100000Z-")
    assert load_cycle(tmp_path, cycle_id)["project_id"] == "demo-project"
    with pytest.raises(DataError):
        start_cycle(tmp_path, "Bad Project")
    with pytest.raises(NotFoundError):
        load_cycle(tmp_path, "cyc-20261027T100000Z-ffffffff")
    with pytest.raises(DataError):
        load_cycle(tmp_path, "../escape")


def test_snapshot_is_stored_with_traceability(tmp_path, cycle):
    net = FakeNet({("docs.example", "/a"): redirect("/b"), ("docs.example", "/b"): html()})
    snapshot, created = fetch(tmp_path, cycle, "https://docs.example/a#section", net)

    assert created is True
    assert snapshot.id == snapshot_id_for("https://docs.example/a")
    assert snapshot.url == "https://docs.example/a"
    assert snapshot.final_url == "https://docs.example/b"
    assert snapshot.retrieved_at == "2026-10-27T10:00:00Z"
    assert snapshot.content_bytes == len(PAGE)
    assert "shrinks failing cases" in snapshot.text

    path = tmp_path / "research" / cycle / "snapshots" / f"{snapshot.id}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["content_sha256"] == __import__("hashlib").sha256(PAGE).hexdigest()
    assert "<script>" not in path.read_text(encoding="utf-8")  # raw HTML is not kept
    assert load_snapshot(tmp_path, cycle, snapshot.id) == snapshot
    assert list_snapshots(tmp_path, cycle) == (snapshot,)


def test_same_url_is_not_fetched_twice(tmp_path, cycle):
    net = FakeNet({("docs.example", "/a"): html()})
    first, _ = fetch(tmp_path, cycle, "https://docs.example/a", net)
    again, created = fetch(tmp_path, cycle, "https://docs.example/a#other", net)
    assert created is False and again == first
    assert len(net.calls) == 1


def test_snapshot_limit_per_cycle(tmp_path, cycle):
    net = FakeNet({("docs.example", f"/{i}"): html() for i in range(MAX_SNAPSHOTS_PER_CYCLE + 1)})
    for i in range(MAX_SNAPSHOTS_PER_CYCLE):
        fetch(tmp_path, cycle, f"https://docs.example/{i}", net)
    with pytest.raises(FetchError, match="maximum"):
        fetch(tmp_path, cycle, f"https://docs.example/{MAX_SNAPSHOTS_PER_CYCLE}", net)
    assert len(net.calls) == MAX_SNAPSHOTS_PER_CYCLE


def test_unsafe_url_is_refused_before_any_request(tmp_path, cycle):
    net = FakeNet(addresses={"internal.example": ["192.168.0.2"]})
    for url in ("http://docs.example/", "https://internal.example/"):
        with pytest.raises(FetchError):
            fetch(tmp_path, cycle, url, net)
    assert net.calls == []
    assert not (tmp_path / "research" / cycle / "snapshots").exists()


def test_fetch_needs_an_existing_cycle(tmp_path):
    with pytest.raises(NotFoundError):
        fetch(tmp_path, "cyc-20261027T100000Z-ffffffff", "https://docs.example/", FakeNet())


def test_failed_fetch_or_write_leaves_nothing(tmp_path, cycle, monkeypatch):
    net = FakeNet({("docs.example", "/a"): html(status=500)})
    with pytest.raises(FetchError):
        fetch(tmp_path, cycle, "https://docs.example/a", net)

    def broken_fsync(fd):
        raise OSError("disk full")

    monkeypatch.setattr(common.os, "fsync", broken_fsync)
    with pytest.raises(OSError):
        fetch(tmp_path, cycle, "https://docs.example/b", FakeNet({("docs.example", "/b"): html()}))
    assert os.listdir(tmp_path / "research" / cycle / "snapshots") == []


def test_changed_snapshot_is_detected(tmp_path, cycle):
    snapshot, _ = fetch(tmp_path, cycle, "https://docs.example/a", FakeNet({("docs.example", "/a"): html()}))
    path = tmp_path / "research" / cycle / "snapshots" / f"{snapshot.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["text"] = data["text"].replace("shrinks", "deletes")
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataError, match="text_sha256"):
        load_snapshot(tmp_path, cycle, snapshot.id)


def remove_link(path):
    """Remove a directory symlink (os.rmdir on Windows, unlink elsewhere)."""
    if os.name == "nt":
        os.rmdir(path)
    else:
        path.unlink()


def test_linked_research_or_snapshot_folders_are_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "data"
    root.mkdir()
    try:
        os.symlink(outside, root / "research", target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create symlinks here: {e}")
    with pytest.raises(DataError, match="link to another location"):
        start_cycle(root, "demo-project", now=NOW)
    assert os.listdir(outside) == []

    remove_link(root / "research")
    cycle_id = start_cycle(root, "demo-project", now=NOW)
    os.symlink(outside, root / "research" / cycle_id / "snapshots", target_is_directory=True)
    with pytest.raises(DataError, match="link to another location"):
        fetch(root, cycle_id, "https://docs.example/a", FakeNet({("docs.example", "/a"): html()}))
    assert os.listdir(outside) == []


# --- quotes ----------------------------------------------------------------


@pytest.fixture
def snapshot(tmp_path, cycle):
    body = "<p>It “shrinks” failing cases — to a   minimal example.</p>".encode("utf-8")
    net = FakeNet({("docs.example", "/q"): html(body)})
    return fetch(tmp_path, cycle, "https://docs.example/q", net)[0]


def test_quote_found_with_normalized_spacing_quotes_and_dashes(snapshot):
    assert quote_problem(snapshot, 'It "shrinks" failing cases - to a minimal example') is None


@pytest.mark.parametrize(
    "quote, message",
    [
        ('It "SHRINKS" failing cases - to a minimal example', "not found"),  # case matters
        ("It removes failing cases entirely", "not found"),
        ("shrinks", "shorter than"),
        (None, "must be text"),
    ],
)
def test_quote_problems(snapshot, quote, message):
    assert message in quote_problem(snapshot, quote)


def test_text_from_dropped_elements_cannot_be_quoted(tmp_path, cycle):
    snapshot, _ = fetch(tmp_path, cycle, "https://docs.example/a", FakeNet({("docs.example", "/a"): html()}))
    assert "not found" in quote_problem(snapshot, 'var secret = "never shown in text"')


# --- command line ----------------------------------------------------------


def test_cli_start_and_fetch(tmp_path, capsys, monkeypatch):
    net = FakeNet({("docs.example", "/a"): html()}, {"internal.example": ["127.0.0.1"]})
    monkeypatch.setattr(snap_module, "default_resolver", net.resolver)
    monkeypatch.setattr(snap_module, "default_transport", net.transport)

    assert main(["start", "--data-root", str(tmp_path), "--project-id", "demo-project"]) == 0
    cycle_id = capsys.readouterr().out.strip()
    assert main(["fetch", "--data-root", str(tmp_path), "--cycle-id", cycle_id, "https://docs.example/a"]) == 0
    assert "stored" in capsys.readouterr().out

    code = main(
        ["fetch", "--data-root", str(tmp_path), "--cycle-id", cycle_id, "https://docs.example/a", "https://internal.example/"]
    )
    out, err = capsys.readouterr()
    assert code == 1 and "already stored" in out and "not a public address" in err
