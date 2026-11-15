"""Синтетические пакеты, подставные HTTPS-ответы и монотонные часы."""

import hashlib
import io
import urllib.error
import urllib.request
import zipfile
from email.message import Message
from types import SimpleNamespace

import pytest

from iphone_carrier_manager import fetch, profiles
from iphone_carrier_manager.ipcc import PackageError


def selected(data):
    return profiles.parse_database(data).profiles[0]


def test_download_ipcc(profile_data, zip_bytes, network):
    item = selected(profile_data)
    payload = zip_bytes()
    network.bodies[item.source.url] = payload
    data, package = fetch.fetch_profile(item)
    assert data == payload
    assert package.identifier == item.bundle_id
    assert package.digest == hashlib.sha256(payload).hexdigest()
    assert network.closed == 1
    assert network.opens == 1
    assert all(0 < timeout <= 30 for timeout in network.timeouts)


@pytest.mark.parametrize("name", ["sha256", "sha1", "sha384"])
@pytest.mark.parametrize("valid", [False, True])
def test_hashes_before_inspection(profile_data, zip_bytes, network, monkeypatch, name, valid):
    payload = zip_bytes()
    source = profile_data["profiles"][0]["source"]
    source[name] = hashlib.new(name, payload if valid else b"other").hexdigest()
    item = selected(profile_data)
    network.bodies[item.source.url] = payload
    if valid:
        assert fetch.fetch_profile(item)[0] == payload
    else:
        monkeypatch.setattr(fetch, "inspect_bytes", lambda data: pytest.fail("разбор до хеша"))
        with pytest.raises(profiles.ProfileError, match=name.upper().replace("SHA", "SHA-")):
            fetch.fetch_profile(item)


@pytest.mark.parametrize("delta", [-1, 1])
def test_size_mismatch(profile_data, zip_bytes, network, delta):
    profile_data["profiles"][0]["source"]["size"] += delta
    item = selected(profile_data)
    network.bodies[item.source.url] = zip_bytes()
    with pytest.raises(profiles.ProfileError, match="[Рр]азмер"):
        fetch.fetch_profile(item)


def test_stream_limit(profile_data, zip_bytes, network, monkeypatch):
    item = selected(profile_data)
    network.bodies[item.source.url] = zip_bytes()
    monkeypatch.setattr(fetch, "MAX_ARCHIVE", 10)
    with pytest.raises(profiles.ProfileError, match="предел"):
        fetch.fetch_profile(item)
    assert network.closed == 1


@pytest.mark.parametrize("field,value", [("bundle_id", "example.other"), ("version", "9.0")])
def test_metadata_mismatch(profile_data, zip_bytes, network, field, value):
    profile_data["profiles"][0][field] = value
    item = selected(profile_data)
    network.bodies[item.source.url] = zip_bytes()
    with pytest.raises(profiles.ProfileError, match="Идентификатор или версия"):
        fetch.fetch_profile(item)


def test_unknown_structure_code(profile_data, zip_bytes, network):
    payload = zip_bytes(extra={"unknown.bin": b"synthetic"})
    source = profile_data["profiles"][0]["source"]
    source.update(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    item = selected(profile_data)
    network.bodies[item.source.url] = payload
    with pytest.raises(PackageError) as error:
        fetch.fetch_profile(item)
    assert error.value.code == 3


def setup_files(files_data, network):
    data, content = files_data
    item = selected(data)
    for file in item.source.files:
        url = (
            f"https://raw.githubusercontent.com/{item.source.repo}/{item.source.commit}/"
            + file.path.replace(" ", "%20")
        )
        network.bodies[url] = content[file.relative_path]
    return item


def test_files_success_space_and_determinism(files_data, network):
    item = setup_files(files_data, network)
    first, package = fetch.fetch_profile(item)
    second, _ = fetch.fetch_profile(item)
    assert first == second
    assert package.identifier == "example.target"
    assert all(url.startswith("https://raw.githubusercontent.com/") for url in network.urls)
    assert all("Carrier%20Bundles/" in url and " " not in url for url in network.urls)
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == [
            "Payload/",
            "Payload/Synthetic.bundle/",
            "Payload/Synthetic.bundle/nested/",
            "Payload/Synthetic.bundle/nested/deeper/",
            "Payload/Synthetic.bundle/Info.plist",
            "Payload/Synthetic.bundle/carrier.plist",
            "Payload/Synthetic.bundle/nested/deeper/test.bin",
        ]
        assert archive.read("Payload/Synthetic.bundle/Info.plist") == files_data[1]["Info.plist"]
    content = files_data[1]
    assert first == fetch.build_ipcc(item.source, dict(reversed(list(content.items()))))


def test_files_hash_mismatch_stops_immediately(files_data, network):
    item = setup_files(files_data, network)
    network.bodies[next(iter(network.bodies))] = b"x" * item.source.files[0].size
    with pytest.raises(profiles.ProfileError, match="SHA-256"):
        fetch.fetch_profile(item)
    assert network.opens == 1


@pytest.mark.parametrize("limit", ["MAX_ARCHIVE", "MAX_EXPANDED", "MAX_ENTRIES"])
def test_build_limits(files_data, network, monkeypatch, limit):
    item = setup_files(files_data, network)
    monkeypatch.setattr(fetch, limit, 1)
    with pytest.raises(profiles.ProfileError, match="[Пп]ревыш|превыш"):
        fetch.build_ipcc(item.source, files_data[1])


def test_file_sum_limit_before_network(files_data, monkeypatch, network):
    item = setup_files(files_data, network)
    monkeypatch.setattr(fetch, "MAX_EXPANDED", 1)
    with pytest.raises(profiles.ProfileError, match="Суммарный"):
        fetch.fetch_profile(item)
    assert network.opens == 0


def test_slow_small_blocks_exceed_file_deadline(profile_data, zip_bytes, network):
    item = selected(profile_data)
    network.bodies[item.source.url] = zip_bytes()
    network.step = 25
    network.block = 1
    with pytest.raises(profiles.ProfileError, match="общий предел времени") as error:
        fetch.fetch_profile(item)
    assert network.now == 125
    assert max(network.timeouts) <= 30
    assert min(network.timeouts) == 20
    assert item.id in str(error.value)
    assert "updates.cdn-apple.com" in str(error.value)
    assert "интернет, VPN или прокси" in str(error.value)
    assert network.opens == 1
    assert network.closed == 1


def test_profile_budget_expires_on_second_file(files_data, network, monkeypatch):
    item = setup_files(files_data, network)
    network.step = 20
    original = fetch.download_bytes
    completed = []

    def download(*args, **kwargs):
        result = original(*args, **kwargs)
        completed.append(result)
        if len(completed) == 1:
            # Пауза процесса между файлами входит в бюджет профиля, но не файла.
            network.now += 220
        return result

    monkeypatch.setattr(fetch, "download_bytes", download)
    with pytest.raises(profiles.ProfileError, match="общий предел времени") as error:
        fetch.fetch_profile(item)
    assert network.now == 300
    assert len(completed) == 1
    assert len(network.urls) == 2
    assert "raw.githubusercontent.com" in str(error.value)
    assert item.id in str(error.value)
    assert network.closed == 2


def test_timeout_defaults():
    assert (fetch.IO_TIMEOUT, fetch.FILE_TIMEOUT, fetch.PROFILE_TIMEOUT) == (30, 120, 300)


@pytest.mark.parametrize(
    "exception",
    [
        TimeoutError("secret?token=private"),
        urllib.error.URLError("secret?token=private"),
        OSError("secret?token=private"),
        urllib.error.HTTPError("https://example.invalid/?secret", 403, "secret", {}, None),
    ],
)
def test_network_error_safe_without_retry(profile_data, network, exception):
    network.error = exception
    with pytest.raises(profiles.ProfileError) as error:
        fetch.fetch_profile(selected(profile_data))
    assert "интернет, VPN или прокси" in str(error.value)
    assert "secret" not in str(error.value)
    assert "https://" not in str(error.value)
    assert network.opens == 1


@pytest.mark.parametrize(
    "url", ["http://raw.githubusercontent.com/test", "https://foreign.invalid/test"]
)
def test_bad_url_before_open(network, url):
    with pytest.raises(profiles.ProfileError):
        fetch.download_bytes(url, 1, {"sha256": "0" * 64}, label="synthetic", profile_deadline=300)
    assert network.opens == 0


@pytest.mark.parametrize(
    "target,allowed",
    [
        ("https://raw.githubusercontent.com/synthetic", True),
        ("/relative", True),
        ("https://foreign.invalid/?private=secret", False),
        ("http://raw.githubusercontent.com/test", False),
        ("https://raw.githubusercontent.com/\ntest", False),
    ],
)
def test_redirect_before_request(target, allowed):
    budget = fetch.DownloadBudget(
        fetch.time.monotonic() + 300, "synthetic", "updates.cdn-apple.com"
    )
    handler = fetch.AllowedRedirectHandler(budget)
    calls = []
    handler.parent = SimpleNamespace(open=lambda req, timeout: calls.append(req.full_url))
    headers = Message()
    headers["Location"] = target
    fp = io.BytesIO(b"must not be read")
    request = urllib.request.Request("https://updates.cdn-apple.com/synthetic")
    if allowed:
        handler.http_error_302(request, fp, 302, "Found", headers)
        assert len(calls) == 1
    else:
        with pytest.raises(profiles.ProfileError):
            handler.http_error_302(request, fp, 302, "Found", headers)
        assert calls == []
    assert fp.closed


def test_redirect_loop_limit():
    budget = fetch.DownloadBudget(
        fetch.time.monotonic() + 300, "synthetic", "updates.cdn-apple.com"
    )
    handler = fetch.AllowedRedirectHandler(budget)
    for _ in range(10):
        handler.redirect_request(None, None, 302, "", {}, "https://updates.cdn-apple.com/loop")
    with pytest.raises(profiles.ProfileError, match="предел перенаправлений"):
        handler.redirect_request(None, None, 302, "", {}, "https://updates.cdn-apple.com/loop")


def test_real_urllib_handlers_with_memory_socket(profile_data, zip_bytes, monkeypatch):
    payload = zip_bytes()
    urls = []
    timeouts = []

    class MemorySocket:
        def makefile(self, mode):
            body = (
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )
            return io.BytesIO(body)

        def sendall(self, data):
            urls.append(data)

        def close(self):
            pass

        def settimeout(self, timeout):
            timeouts.append(timeout)

    def connect(connection):
        connection.sock = MemorySocket()

    monkeypatch.setattr(fetch.TimedHTTPSConnection, "connect", connect)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    result, _ = fetch.fetch_profile(selected(profile_data))
    assert result == payload
    assert b"GET /synthetic.ipcc HTTP/1.1" in urls[0]
    assert all(0 < timeout <= 30 for timeout in timeouts)


def test_save_no_overwrite_or_race(tmp_path):
    path = tmp_path / "synthetic.ipcc"
    fetch.save_ipcc(path, b"first")
    with pytest.raises(profiles.ProfileError, match="уже существует"):
        fetch.save_ipcc(path, b"second")
    assert path.read_bytes() == b"first"


def test_save_partial_cleanup(tmp_path, monkeypatch):
    from pathlib import Path

    path = tmp_path / "synthetic.ipcc"
    original = Path.open

    class BrokenWrite:
        def __enter__(self):
            self.stream = original(path, "xb")
            return self

        def write(self, data):
            self.stream.write(data[:1])
            raise OSError("private")

        def __exit__(self, *args):
            self.stream.close()

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: BrokenWrite())
    with pytest.raises(profiles.ProfileError, match="сохранить"):
        fetch.save_ipcc(path, b"synthetic")
    assert not path.exists()


def test_deadline_in_slow_http_headers(monkeypatch):
    now = [0]
    timeouts = []
    monkeypatch.setattr(fetch.time, "monotonic", lambda: now[0])

    class SlowHeaders(io.BytesIO):
        def read1(self, size=-1):
            now[0] += 25
            return super().read1(1)

    sock = SimpleNamespace(
        makefile=lambda mode: SlowHeaders(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"),
        settimeout=timeouts.append,
    )
    budget = fetch.DownloadBudget(300, "synthetic-stock", "raw.githubusercontent.com")
    response = fetch.TimedHTTPResponse(sock, budget=budget)
    try:
        with pytest.raises(profiles.ProfileError, match="общий предел времени"):
            response.begin()
    finally:
        response.close()
    assert now[0] == 125
    assert min(timeouts) == 20


@pytest.mark.parametrize("foreign", [False, True])
def test_urllib_redirect_chain_without_network(profile_data, zip_bytes, monkeypatch, foreign):
    payload = zip_bytes()
    connections = []

    class MemorySocket:
        def __init__(self, host):
            self.host = host

        def makefile(self, mode):
            if self.host == "updates.cdn-apple.com":
                target = "foreign.invalid" if foreign else "raw.githubusercontent.com"
                # Неограниченное тело не читается перед редиректом.
                return io.BytesIO(
                    b"HTTP/1.1 302 Found\r\nLocation: https://"
                    + target.encode()
                    + b"/synthetic\r\n\r\nmust not be read"
                )
            return io.BytesIO(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )

        def sendall(self, data):
            pass

        def close(self):
            pass

        def settimeout(self, timeout):
            pass

    def connect(connection):
        connections.append(connection.host)
        connection.sock = MemorySocket(connection.host)

    monkeypatch.setattr(fetch.TimedHTTPSConnection, "connect", connect)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    if foreign:
        with pytest.raises(profiles.ProfileError, match="белого списка"):
            fetch.fetch_profile(selected(profile_data))
        assert connections == ["updates.cdn-apple.com"]
    else:
        assert fetch.fetch_profile(selected(profile_data))[0] == payload
        assert connections == ["updates.cdn-apple.com", "raw.githubusercontent.com"]
