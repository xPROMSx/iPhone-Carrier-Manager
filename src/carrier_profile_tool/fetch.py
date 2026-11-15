"""Последовательное скачивание по базе и подготовка IPCC только в памяти."""

import hashlib
import http.client
import io
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote, urljoin

from carrier_profile_tool.ipcc import MAX_ARCHIVE, MAX_ENTRIES, MAX_EXPANDED, inspect_bytes
from carrier_profile_tool.profiles import FilesSource, IpccSource, ProfileError, validate_url

IO_TIMEOUT = 30
FILE_TIMEOUT = 120
PROFILE_TIMEOUT = 300
BLOCK_SIZE = 65536


class DownloadBudget:
    def __init__(self, profile_deadline, label, host):
        self.deadline = min(time.monotonic() + FILE_TIMEOUT, profile_deadline)
        self.label = label
        self.host = host

    def message(self, reason):
        return (
            f"{reason}: профиль «{self.label}», хост {self.host}. "
            "Проверьте интернет, VPN или прокси."
        )

    def timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProfileError(self.message("Превышен общий предел времени скачивания"))
        return min(IO_TIMEOUT, remaining)


class _DeadlineReader(io.RawIOBase):
    """Ограничивает также чтение HTTP-заголовков и заголовков chunked-кодирования."""

    def __init__(self, stream, sock, budget):
        self.stream = stream
        self.sock = sock
        self.budget = budget

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(self.budget.timeout())
        data = self.stream.read1(len(buffer))
        self.budget.timeout()
        buffer[: len(data)] = data
        return len(data)

    def close(self):
        try:
            self.stream.close()
        finally:
            super().close()


class TimedHTTPResponse(http.client.HTTPResponse):
    def __init__(self, sock, *args, budget, **kwargs):
        self.download_socket = sock
        super().__init__(sock, *args, **kwargs)
        self.fp = io.BufferedReader(_DeadlineReader(self.fp, sock, budget))


class TimedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, budget, **kwargs):
        self.budget = budget
        super().__init__(*args, **kwargs)

    def response_class(self, sock, *args, **kwargs):
        return TimedHTTPResponse(sock, *args, budget=self.budget, **kwargs)


class DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def https_open(self, request):
        self.budget.host = validate_url(request.full_url)

        def connection(host, **kwargs):
            kwargs["timeout"] = self.budget.timeout()
            return TimedHTTPSConnection(host, budget=self.budget, **kwargs)

        return self.do_open(connection, request, context=self._context)


class AllowedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, budget):
        self.budget = budget
        self.redirects = 0

    def redirect_request(self, request, fp, code, message, headers, newurl):
        host = validate_url(newurl)
        self.redirects += 1
        if self.redirects > 10:
            raise ProfileError(self.budget.message("Превышен предел перенаправлений"))
        self.budget.host = host
        self.budget.timeout()
        return urllib.request.Request(newurl, method="GET")

    def http_error_302(self, request, fp, code, message, headers):
        # Не читать тело редиректа: оно также может быть бесконечным.
        fp.close()
        target = headers.get("Location") or headers.get("URI")
        if target is None:
            raise ProfileError(self.budget.message("В перенаправлении нет адреса"))
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in target):
            raise ProfileError("Небезопасный адрес перенаправления.")
        target = urljoin(request.full_url, target)
        redirected = self.redirect_request(request, fp, code, message, headers, target)
        return self.parent.open(redirected, timeout=self.budget.timeout())

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _opener(budget):
    return urllib.request.build_opener(AllowedRedirectHandler(budget), DeadlineHTTPSHandler(budget))


def download_bytes(url, size, hashes, *, label, profile_deadline):
    budget = DownloadBudget(profile_deadline, label, validate_url(url))
    digests = {name: hashlib.new(name) for name in hashes}
    data = bytearray()
    try:
        with _opener(budget).open(url, timeout=budget.timeout()) as response:
            while True:
                timeout = budget.timeout()
                response.download_socket.settimeout(timeout)
                # read1 делает не более одного чтения socket; проверяем бюджет на каждом блоке.
                chunk = response.read1(min(BLOCK_SIZE, min(size, MAX_ARCHIVE) - len(data) + 1))
                budget.timeout()
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > min(size, MAX_ARCHIVE):
                    raise ProfileError("Размер скачанных данных превышает size или предел 64 MiB.")
                for digest in digests.values():
                    digest.update(chunk)
                if response.isclosed():
                    break
    except urllib.error.HTTPError as error:
        error.close()
        raise ProfileError(budget.message("Не удалось скачать файл")) from None
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        budget.timeout()
        raise ProfileError(budget.message("Не удалось скачать файл")) from None
    if len(data) != size:
        raise ProfileError("Размер скачанных данных не совпадает с базой.")
    for name, digest in digests.items():
        if digest.hexdigest() != hashes[name]:
            label = name.upper().replace("SHA", "SHA-")
            raise ProfileError(f"{label} скачанных данных не совпадает с базой.")
    return bytes(data)


class _ZipBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_ARCHIVE:
            raise ProfileError("Превышен предел размера собираемого IPCC: 64 MiB.")
        return super().write(data)


def build_ipcc(source, files):
    root = f"Payload/{source.bundle_name}/"
    directories = {"Payload/", root}
    total = 0
    for name, data in files.items():
        total += len(data)
        parts = name.split("/")
        directories.update(root + "/".join(parts[:i]) + "/" for i in range(1, len(parts)))
    if total > MAX_EXPANDED or len(directories) + len(files) > MAX_ENTRIES:
        raise ProfileError("Превышен предел объёма или числа записей собираемого IPCC.")
    buffer = _ZipBuffer()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(directories, key=lambda item: (item.count("/"), item)):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (0o40755 << 16) | 0x10
            archive.writestr(entry, b"")
        for name in sorted(files):
            entry = zipfile.ZipInfo(root + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, files[name])
    return buffer.getvalue()


def fetch_profile(profile):
    deadline = time.monotonic() + PROFILE_TIMEOUT
    source = profile.source
    if isinstance(source, IpccSource):
        data = download_bytes(
            source.url, source.size, source.hashes, label=profile.id, profile_deadline=deadline
        )
    elif isinstance(source, FilesSource):
        if sum(file.size for file in source.files) > MAX_EXPANDED:
            raise ProfileError("Суммарный размер файлов превышает 64 MiB.")
        files = {}
        for file in source.files:
            url = f"https://raw.githubusercontent.com/{source.repo}/{source.commit}/" + quote(
                file.path, safe="/"
            )
            files[file.relative_path] = download_bytes(
                url, file.size, file.hashes, label=profile.id, profile_deadline=deadline
            )
        data = build_ipcc(source, files)
    else:
        raise ProfileError("Неизвестный тип источника профиля.")
    package = inspect_bytes(data)
    if (package.identifier, package.version) != (profile.bundle_id, profile.version):
        raise ProfileError("Идентификатор или версия скачанного профиля не совпадает с базой.")
    return data, package


def save_ipcc(path, data):
    created = False
    try:
        with Path(path).open("xb") as stream:
            created = True
            stream.write(data)
    except FileExistsError:
        raise ProfileError("Выходной файл уже существует; перезапись запрещена.") from None
    except (OSError, ValueError, KeyboardInterrupt):
        if created:
            Path(path).unlink(missing_ok=True)
        raise ProfileError("Не удалось сохранить IPCC в указанный файл.") from None
