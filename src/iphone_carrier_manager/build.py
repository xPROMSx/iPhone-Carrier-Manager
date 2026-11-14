"""Пробная подготовка только в памяти, без установки и экспорта."""

import io
import zipfile

from iphone_carrier_manager.device import choose_line, read_device
from iphone_carrier_manager.ipcc import PackageError, check_file, inspect_bytes

WARNING = (
    "Результат не сохраняется, на iPhone не устанавливался. Успех подготовки не означает "
    "совместимость с iOS и пригодность к установке, в том числе после будущей проверки "
    "подписи. Сейчас подпись не подтверждена."
)


def prepare(package, line):
    prefix = (
        "Payload/"
        + line.international_mobile_subscriber_identity
        + "_ID-"
        + line.integrated_circuit_card_identity
        + "_ID-.bundle/"
    )

    def renamed(name):
        return prefix + name[len(package.bundle) :] if name.startswith(package.bundle) else name

    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in package.directories:
                archive.writestr(renamed(name), b"")
            for name, content in package.files.items():
                archive.writestr(renamed(name), content)
        prepared = inspect_bytes(buffer.getvalue())
        original = {n[len(package.bundle) :]: b for n, b in package.files.items()}
        result = {n[len(prepared.bundle) :]: b for n, b in prepared.files.items()}
        if original != result or (package.identifier, package.version) != (
            prepared.identifier,
            prepared.version,
        ):
            raise PackageError("Внутренние файлы не совпали; подготовка отменена.")
    # Ни буфер, ни полный путь с идентификаторами не покидают эту функцию.


async def build_file(path, selector):
    package = check_file(path)
    device = await read_device(selector)
    line = choose_line(device, selector)
    prepare(package, line)
    return package, device, line
