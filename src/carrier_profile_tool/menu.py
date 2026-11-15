"""Пошаговый выбор одной линии и двух пакетов; пакеты остаются в памяти."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from carrier_profile_tool.cli import safe
from carrier_profile_tool.device import choose_line, read_device
from carrier_profile_tool.fetch import build_ipcc, fetch_profile
from carrier_profile_tool.install import install_packages
from carrier_profile_tool.ipcc import check_file, inspect_bytes
from carrier_profile_tool.profiles import group_for_product, load_database, profiles_for_product


class ExitMenu(Exception):
    def __init__(self, code=0):
        self.code = code


def numbered_choice(value, count):
    """Чистая логика выбора; None означает выход."""
    if value in ("", "0"):
        return None
    if value.isascii() and value.isdecimal() and 1 <= int(value) <= count:
        return int(value) - 1
    raise ValueError


class Console:
    def __init__(self, read, write):
        self.read = read
        self.write = write

    def choose(self, question, count):
        for _ in range(3):
            try:
                selected = numbered_choice(self.read(question).strip(), count)
            except ValueError:
                self.write("Введите номер из списка. Для выхода нажмите Enter или введите 0.")
                continue
            if selected is None:
                raise ExitMenu
            return selected
        self.write("Три неверных ответа. Работа завершена.")
        raise ExitMenu

    def path(self, question):
        value = self.read(question).strip().strip('"')
        if value in ("", "0"):
            self.write("Без пакета для возврата установка не выполняется.")
            raise ExitMenu
        return value


def local_package(value):
    path = Path(value)
    if path.is_file() and path.suffix.lower() == ".ipcc":
        return check_file(path)
    if path.is_dir() and path.suffix.lower() == ".bundle":
        files = {
            item.relative_to(path).as_posix(): item.read_bytes()
            for item in path.rglob("*") if item.is_file()
        }
        if not {"Info.plist", "carrier.plist"} <= files.keys():
            raise ValueError
        return inspect_bytes(build_ipcc(SimpleNamespace(bundle_name=path.name), files))
    raise ValueError


def same_profile(package, line):
    identifier = getattr(package, "bundle_id", None)
    if identifier is None:
        identifier = package.identifier
    return (identifier, package.version) == (line.identifier, line.version)


def download(item, label, console):
    console.write(f"Скачивание: {label}, {safe(item.operator)}, версия {safe(item.version)}.")
    try:
        return fetch_profile(item)[1]
    except Exception:
        console.write(f"Не удалось скачать: {label}. Проверьте интернет, VPN или прокси.")
        raise ExitMenu(1) from None


async def steps(console):
    out = console.write
    out("Carrier Profile Tool помогает выбрать и установить операторский профиль.")
    out("Нужны iPhone по USB и Apple Devices или iTunes. Разблокируйте iPhone")
    out('и подтвердите доверие компьютеру кнопкой «Доверять».')
    console.choose("1 - продолжить, 0 - выход: ", 1)
    while True:
        try:
            phone = await read_device()
            break
        except Exception:
            out("Не удалось прочитать iPhone по USB. Откройте Apple Devices или iTunes,")
            out("дождитесь появления телефона в списке, разблокируйте его")
            out('и нажмите «Доверять». Проверьте USB-кабель.')
            console.choose("1 - повторить, 0 - выход: ", 1)
    out(f"Модель: {safe(phone.product)}; iOS: {safe(phone.ios)}")
    for number, line in enumerate(phone.lines, 1):
        out(f"{number}. slot:{safe(line.slot)}, MCC {safe(line.mcc)}, MNC {safe(line.mnc)}; "
            f"профиль {safe(line.identifier)}, версия {safe(line.version)}")
    if phone.line_problem or not phone.lines:
        out("Линия не определена однозначно. Установка не выполняется.")
        return 1
    index = console.choose(
        "1 - выбрать эту линию, 0 - выход: " if len(phone.lines) == 1
        else "Номер линии (0 - выход): ", len(phone.lines)
    )
    line = phone.lines[index]
    selector = "slot:" + line.slot
    choose_line(phone, selector)
    database = load_database()
    items = profiles_for_product(database, phone.product)
    group = group_for_product(database, phone.product)
    tested = group is not None and phone.product in database.groups[group].tested
    status = "проверялось на этой модели" if tested else "не проверялось на этой модели"
    for number, item in enumerate(items, 1):
        kind = "штатный" if item.kind == "stock" else "донор"
        out(f"{number}. {safe(item.operator)}, версия {safe(item.version)}, {kind}; "
            f"{safe(item.note or 'без примечания')}; {status}")
    out(f"{len(items) + 1}. Указать свой файл (IPCC или папку .bundle)")
    selected = console.choose("Номер профиля (0 - выход): ", len(items) + 1)
    if selected < len(items):
        item = items[selected]
        if same_profile(item, line):
            out("Этот профиль уже установлен на выбранной линии.")
            return 0
        source = download(item, "новый профиль", console)
        untested = not tested
    else:
        source = local_package(console.path("Путь к IPCC или папке .bundle (0 - выход): "))
        untested = True
    if same_profile(source, line):
        out("Этот профиль уже установлен на выбранной линии.")
        return 0
    previous_item = next((item for item in items if item.bundle_id == line.identifier), None)
    if previous_item is not None:
        previous = download(previous_item, "пакет для возврата", console)
    else:
        out("В базе нет прежнего профиля для этой модели.")
        previous = local_package(console.path(
            "Укажите прежний IPCC или папку .bundle (Enter - выход): "
        ))
    out(f"Выбрана линия slot:{safe(line.slot)}.")
    out(f"Текущий профиль: {safe(line.identifier)}, версия {safe(line.version)}.")
    out(f"Новый профиль: {safe(source.identifier)}, версия {safe(source.version)}.")
    out(f"Пакет для возврата: {safe(previous.identifier)}, версия {safe(previous.version)}.")
    out("Возврат прежнего профиля (не гарантирован).")
    if untested:
        out("Внимание: не проверялось на этой модели.")
    return await install_packages(source, previous, selector)


async def run_menu(read=None, write=None):
    console = Console(read or input, write or print)
    try:
        try:
            result = await steps(console)
        except ExitMenu as error:
            if error.code == 0:
                return 0
            result = error.code
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception:
            console.write(
                "Операция не выполнена. Проверьте выбранные файлы и подключение телефона."
            )
            result = 1
        console.read("Нажмите Enter, чтобы закрыть окно")
        return result
    except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
        console.write("Операция отменена")
        return 1
