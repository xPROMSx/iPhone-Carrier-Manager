"""Минимальная русскоязычная командная строка."""

import argparse
import asyncio
import re
import sys
from pathlib import Path

from iphone_carrier_manager import __version__
from iphone_carrier_manager.ipcc import SIGNATURE_REASON, PackageError, check_file


def safe(value):
    text = str(value)
    if re.search(r"[0-9]{5,}", text) or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text):
        return "<скрыто>"
    return text[:120]


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "Аргументы"
        self._optionals.title = "Параметры"
        self.add_argument("-h", "--help", action="help", help="Показать справку и выйти")

    def format_help(self):
        return super().format_help().replace("usage:", "Использование:", 1)

    def error(self, message):
        # argparse включает исходные аргументы в ошибки: они могут содержать секреты.
        self.exit(2, "Неверные аргументы. Используйте --help.\n")


def parser():
    result = Parser(prog="iphone-carrier-manager", description="Чтение и пробная подготовка IPCC.")
    result.add_argument("--version", action="version", version=__version__, help="Показать версию")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("info", help="Прочитать ранее сопряжённый iPhone через USB")
    check = commands.add_parser("check", help="Проверить локальный IPCC или каталог")
    check.add_argument("path", help="IPCC или каталог")
    build = commands.add_parser("build", help="Пробно подготовить IPCC только в памяти")
    build.add_argument("--source", required=True, help="Исходный IPCC")
    build.add_argument("--line", required=True, help="Точный селектор, например slot:kOne")
    return result


def profile(label, identifier, version):
    print(f"{label}: {safe(identifier)}, версия {safe(version)}")


def check_path(path):
    path = Path(path)
    if path.is_dir():
        paths = sorted(
            (p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".ipcc"),
            key=lambda p: p.name,
        )
    else:
        paths = [path]
    if not paths:
        raise PackageError("В каталоге нет файлов IPCC.")
    codes = []
    for number, source in enumerate(paths, 1):
        print(f"Файл №{number}")
        try:
            package = check_file(source)
            profile("Целевой профиль", package.identifier, package.version)
            print("Структура: проверена")
            print(f"Подпись: {package.signature_status}: {SIGNATURE_REASON}")
            print("Файлы подписи: " + (", ".join(safe(n) for n in package.signatures) or "нет"))
            print(f"SHA-256 исходного файла (не доказательство подлинности): {package.digest}")
            codes.append(3)
        except PackageError as error:
            print(f"Проверка не завершена: {error}")
            if hasattr(error, "digest"):
                print(f"SHA-256 исходного файла (не доказательство подлинности): {error.digest}")
            codes.append(error.code)
    return 1 if 1 in codes else 3


async def device_command(args):
    from iphone_carrier_manager.build import WARNING, build_file
    from iphone_carrier_manager.device import read_device

    if args.command == "info":
        device = await read_device()
        print(
            f"Модель: {safe(device.product)}; iOS: {safe(device.ios)}; build: {safe(device.build)}"
        )
        if device.line_problem:
            print("Линия не определена: неполные или неоднозначные данные.")
        for line in device.lines:
            print(f"Линия slot:{safe(line.slot)}; MCC {safe(line.mcc)}; MNC {safe(line.mnc)}")
            profile("Текущий профиль", line.identifier, line.version)
        print("Полнота списка и активность линий не подтверждены.")
    else:
        package, device, line = await build_file(args.source, args.line)
        profile("Текущий профиль линии", line.identifier, line.version)
        profile("Целевой профиль", package.identifier, package.version)
        print(f"Линия: slot:{safe(line.slot)}")
        print(
            "Маска внешнего имени: абонентский идентификатор: "
            f"{len(line.international_mobile_subscriber_identity)} цифр, идентификатор SIM: "
            f"{len(line.integrated_circuit_card_identity)} цифр"
        )
        print("Структура: проверена. Внутренние файлы совпали побайтно.")
        print(f"Подпись: UNVERIFIED: {SIGNATURE_REASON}")
        print("Пробная подготовка выполнена")
        print(WARNING)
    if device.ios != "27.2.1":
        print("Предупреждение: совместимость с этой версией iOS не проверена.")
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "check":
            return check_path(args.path)
        return asyncio.run(device_command(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Операция отменена; работа не продолжается.", file=sys.stderr)
        return 1
    except PackageError as error:
        prefix = "Пробная подготовка не выполнена" if args.command == "build" else "Ошибка"
        print(f"{prefix}: {error}", file=sys.stderr)
        return error.code
    except Exception as error:
        from iphone_carrier_manager.device import DeviceError

        message = str(error) if isinstance(error, DeviceError) else "Операция не выполнена."
        prefix = "Пробная подготовка не выполнена" if args.command == "build" else "Ошибка"
        print(f"{prefix}: {message}", file=sys.stderr)
        return 1
