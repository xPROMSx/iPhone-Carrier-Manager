"""Минимальная русскоязычная командная строка."""

import argparse
import asyncio
import os
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
    result = Parser(
        prog="iphone-carrier-manager", description="Чтение, подготовка и установка IPCC."
    )
    result.add_argument("--version", action="version", version=__version__, help="Показать версию")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("info", help="Прочитать ранее сопряжённый iPhone через USB")
    check = commands.add_parser("check", help="Проверить локальный IPCC или каталог")
    check.add_argument("path", help="IPCC или каталог")
    build = commands.add_parser("build", help="Пробно подготовить IPCC только в памяти")
    build.add_argument("--source", required=True, help="Исходный IPCC")
    build.add_argument("--line", required=True, help="Точный селектор, например slot:kOne")
    install = commands.add_parser(
        "install", help="Установить IPCC на одну линию и перезагрузить iPhone"
    )
    install.add_argument("--source", required=True, help="Устанавливаемый IPCC")
    install.add_argument("--line", required=True, help="Точный селектор, например slot:kOne")
    install.add_argument("--previous", required=True, help="IPCC для возврата к текущему профилю")
    profiles = commands.add_parser("profiles", help="Показать профили из базы для модели")
    profiles.add_argument("--product", help="ProductType для работы без телефона")
    fetch = commands.add_parser("fetch", help="Скачать и проверить профиль из базы")
    fetch.add_argument("id", help="id профиля из базы")
    fetch.add_argument("--out", required=True, help="Выходной файл IPCC без перезаписи")
    return result


async def profiles_command(args):
    from iphone_carrier_manager.profiles import (
        group_for_product,
        load_database,
        profiles_for_product,
    )

    database = load_database()
    if not database.profiles:
        print("База профилей пуста")
        return 0
    product = args.product
    if product is None:
        from iphone_carrier_manager.device import read_device

        product = (await read_device()).product
    selected = profiles_for_product(database, product)
    if not selected:
        print(f"Для модели {safe(product)} профилей нет.")
        return 0
    group = database.groups[group_for_product(database, product)]
    print(f"Группа: {safe(group.label)}")
    for item in selected:
        print(
            f"id: {safe(item.id)}; оператор: {safe(item.operator)}; "
            f"версия: {safe(item.version)}; kind: {safe(item.kind)}"
        )
        if item.note:
            print(f"Примечание: {safe(item.note)}")
        print(
            "проверялось на этой модели" if product in group.tested
            else "не проверялось на этой модели"
        )
    return 0


def fetch_command(args):
    from iphone_carrier_manager.fetch import fetch_profile, save_ipcc
    from iphone_carrier_manager.profiles import ProfileError, get_profile, load_database

    item = get_profile(load_database(), args.id)
    # lexists учитывает также существующую, но неработающую символьную ссылку.
    if os.path.lexists(args.out):
        raise ProfileError("Выходной файл уже существует; перезапись запрещена.")
    data, package = fetch_profile(item)
    save_ipcc(args.out, data)
    print(f"id: {safe(item.id)}; версия: {safe(package.version)}")
    print(f"SHA-256 результата: {package.digest}")
    return 0


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
        if args.command == "profiles":
            return asyncio.run(profiles_command(args))
        if args.command == "fetch":
            return fetch_command(args)
        if args.command == "install":
            from iphone_carrier_manager.install import install_file

            return asyncio.run(install_file(args.source, args.line, args.previous))
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
