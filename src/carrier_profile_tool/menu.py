"""Пошаговый выбор одной линии и двух пакетов; пакеты остаются в памяти."""

import asyncio
import re
import textwrap
from pathlib import Path
from types import SimpleNamespace

from carrier_profile_tool.cli import safe
from carrier_profile_tool.device import choose_line, read_device
from carrier_profile_tool.fetch import build_ipcc, fetch_profile
from carrier_profile_tool.install import install_packages
from carrier_profile_tool.ipcc import check_file, inspect_bytes
from carrier_profile_tool.profiles import group_for_product, load_database, profiles_for_product

NAMES = {
    "MTS_ru": "МТС", "MegaFon_ru": "МегаФон", "Tele2_ru": "Tele2",
    "VimpelCom_ru": "Билайн (VimpelCom)", "Vodafone_hu": "Vodafone Hungary",
    "Vodafone_ro": "Vodafone Romania", "MTS_ua": "MTS Ukraine",
}


def wrapped(write, text, indent=""):
    for line in textwrap.wrap(text, width=78, initial_indent=indent,
                              subsequent_indent=indent):
        write(line)


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
    name = NAMES.get(item.operator, item.operator)
    wrapped(console.write, f"Скачиваю: {safe(name)}, версия {safe(item.version)}...")
    try:
        result = fetch_profile(item)[1]
    except Exception:
        wrapped(console.write, f"Не удалось скачать: {label}. Проверьте интернет, VPN или прокси.")
        raise ExitMenu(1) from None
    console.write("Готово.")
    return result


async def steps(console):
    out = console.write
    out("=" * 62)
    out("  Carrier Profile Tool")
    out("  Выбор и установка операторского профиля на iPhone")
    out("=" * 62)
    out("")
    out("Что понадобится:")
    out("  - iPhone, подключённый по USB и разблокированный;")
    out("  - Apple Devices или iTunes на этом компьютере.")
    out('Если телефон спросит, нажмите на нём "Доверять".')
    out("")
    console.choose("1 - продолжить, 0 - выход: ", 1)
    out("")
    out("Шаг 1 из 4. Телефон")
    out("-" * 62)
    while True:
        try:
            phone = await read_device()
            break
        except Exception:
            out("")
            out("Не удалось прочитать iPhone по USB. Проверьте кабель и откройте")
            out("Apple Devices или iTunes. Дождитесь появления телефона, разблокируйте")
            out('его и нажмите на нём "Доверять".')
            out("")
            console.choose("1 - повторить, 0 - выход: ", 1)
    wrapped(out, f"Модель: {safe(phone.product)}    iOS: {safe(phone.ios)}")
    out("")
    for number, line in enumerate(phone.lines, 1):
        identifier = safe(line.identifier).removeprefix("com.apple.")
        wrapped(out, f"{number}. slot:{safe(line.slot)}  (MCC {safe(line.mcc)}, "
                f"MNC {safe(line.mnc)})  профиль {identifier}, версия {safe(line.version)}", "  ")
    if phone.line_problem or not phone.lines:
        out("Линия не определена однозначно. Установка не выполняется.")
        return 1
    out("")
    index = console.choose(
        "1 - выбрать эту линию, 0 - выход: " if len(phone.lines) == 1
        else "Номер линии (0 - выход): ", len(phone.lines)
    )
    line = phone.lines[index]
    selector = "slot:" + line.slot
    choose_line(phone, selector)
    database = load_database()
    profiles = profiles_for_product(database, phone.product)
    items = tuple(item for kind in ("stock", "donor") for item in profiles if item.kind == kind)
    group = group_for_product(database, phone.product)
    tested = group is not None and phone.product in database.groups[group].tested
    out("")
    out("Шаг 2 из 4. Выбор профиля")
    out("-" * 62)
    for kind, title in (("stock", "Российские операторы:"), ("donor", "Другие операторы:")):
        out("")
        out(title)
        for number, item in enumerate(items, 1):
            if item.kind == kind:
                name = NAMES.get(item.operator, item.operator)
                wrapped(out, f"{number}. {safe(name)}, версия {safe(item.version)}", "  ")
                if item.note:
                    note = re.sub(r"\bштатный\s+", "", safe(item.note), flags=re.IGNORECASE)
                    wrapped(out, note, "     ")
    out("")
    out("Свой файл:")
    out(f"  {len(items) + 1}. Указать свой файл (IPCC или папку .bundle)")
    out("")
    selected = console.choose("Номер профиля (0 - выход): ", len(items) + 1)
    from_database = selected < len(items)
    out("")
    out("Шаг 3 из 4. Загрузка")
    out("-" * 62)
    if from_database:
        item = items[selected]
        if same_profile(item, line):
            out("Этот профиль уже установлен на выбранной линии.")
            return 0
        source = download(item, "новый профиль", console)
    else:
        source = local_package(console.path("Путь к IPCC или папке .bundle (0 - выход): "))
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
    out("")
    out("Шаг 4 из 4. Проверьте выбор")
    out("-" * 62)
    return await install_packages(source, previous, selector, plain=True,
                                  tested=tested, from_database=from_database)


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
            wrapped(console.write,
                    "Операция не выполнена. Проверьте выбранные файлы и подключение телефона.")
            result = 1
        console.write("")
        console.read("Нажмите Enter, чтобы закрыть окно")
        return result
    except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
        console.write("Операция отменена")
        return 1
