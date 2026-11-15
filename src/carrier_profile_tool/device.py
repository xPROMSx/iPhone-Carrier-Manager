"""Только чтение существующей USB-сессии lockdown."""

import asyncio
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from carrier_profile_tool import timeouts

ENDPOINT = "127.0.0.1:27015"
IMSI_KEY = "InternationalMobileSubscriberIdentity"
ICCID_KEY = "IntegratedCircuitCardIdentity"
DIGITS = re.compile(r"[0-9]+\Z")


class DeviceError(Exception):
    pass


@dataclass(repr=False)
class Line:
    slot: str
    identifier: str
    version: str
    mcc: str
    mnc: str
    international_mobile_subscriber_identity: str = field(repr=False)
    integrated_circuit_card_identity: str = field(repr=False)


@dataclass
class Device:
    product: str
    ios: str
    build: str
    lines: list[Line] = field(repr=False)
    line_problem: bool = False


def parse_lines(raw):
    if type(raw) is not list or not raw:
        return [], True
    lines = []
    problem = False
    keys = ("Slot", "CFBundleIdentifier", "CFBundleVersion", "MCC", "MNC", IMSI_KEY, ICCID_KEY)
    for row in raw:
        if type(row) is not dict or type(row.get("Slot")) is not str or not row["Slot"]:
            problem = True
            continue
        complete = all(type(row.get(key)) is str and row[key] for key in keys)
        if not complete:
            problem = True
        lines.append(
            Line(
                *(
                    row[key] if type(row.get(key)) is str and row[key] else "недоступно"
                    for key in keys
                )
            )
        )
    if len({line.slot for line in lines}) != len(lines):
        problem = True
    return lines, problem


def choose_line(device, selector):
    if device.line_problem:
        raise DeviceError("Линия не определена: неполная или неоднозначная схема.")
    matches = [line for line in device.lines if selector == "slot:" + line.slot]
    if len(matches) != 1:
        raise DeviceError("Выбранная линия отсутствует или неоднозначна.")
    line = matches[0]
    for value, minimum, maximum in (
        (line.international_mobile_subscriber_identity, 10, 15),
        (line.integrated_circuit_card_identity, 18, 22),
    ):
        if (
            type(value) is not str
            or not DIGITS.fullmatch(value)
            or not minimum <= len(value) <= maximum
        ):
            raise DeviceError("Идентификаторы линии имеют неподдерживаемый тип или формат.")
    return line


async def _snapshot(client):
    values = client.all_values
    if type(values) is not dict:
        raise DeviceError("Неизвестный формат ответа lockdown.")
    if values.get("DeviceClass") != "iPhone":
        raise DeviceError("USB-устройство не подтверждено как iPhone.")
    raw = values.get("CarrierBundleInfoArray")
    if raw is None:
        try:
            raw = await client.get_value(domain=None, key="CarrierBundleInfoArray")
        except asyncio.CancelledError:
            raise
        except Exception:
            raw = None
    lines, problem = parse_lines(raw)

    def text(key):
        value = values.get(key)
        return value if type(value) is str and value else "недоступно"

    return Device(text("ProductType"), text("ProductVersion"), text("BuildVersion"), lines, problem)


async def open_client(serial, cache):
    """Открыть только существующее сопряжение; клиент закрывает вызывающий код."""
    from pymobiledevice3.lockdown import create_using_usbmux

    client = await create_using_usbmux(
        serial=serial,
        connection_type="USB",
        usbmux_address=ENDPOINT,
        autopair=False,
        pairing_records_cache_folder=cache,
    )
    if not client.paired:
        try:
            await client.close()
        finally:
            raise DeviceError(
                "Нет доверенной сессии. Разрешите доверие штатным способом вне программы "
                "и повторите команду."
            )
    return client


async def _connected(serial, cache, selector):
    client = None
    try:
        client = await open_client(serial, cache)
        first = await _snapshot(client)
        if selector is not None:
            selected = choose_line(first, selector)
            # Свежий ответ в той же сессии. Это не доказательство полноты списка SIM.
            await client.get_value(domain=None, key=None)
            second = await _snapshot(client)
            if selected != choose_line(second, selector) or (
                first.product,
                first.ios,
                first.build,
            ) != (second.product, second.ios, second.build):
                raise DeviceError("Данные выбранной линии изменились; подготовка отменена.")
        return first
    finally:
        if client is not None:
            await client.close()


async def read_device(selector=None):
    # Логи зависимости могут содержать персональные сведения даже при ожидаемом отказе.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        from pymobiledevice3.usbmux import list_devices

        devices = await asyncio.wait_for(list_devices(usbmux_address=ENDPOINT), timeouts.USB)
        usb = [device for device in devices if device.is_usb]
        if not usb:
            raise DeviceError("USB-устройство не найдено. Проверьте кабель и разблокировку.")
        if len(usb) != 1:
            raise DeviceError("Подключено несколько USB-устройств; выбор не выполнен.")
        existing = Path.home() / ".pymobiledevice3"
        if existing.is_dir():
            return await asyncio.wait_for(
                _connected(usb[0].serial, existing, selector), timeouts.READ
            )
        with tempfile.TemporaryDirectory(prefix="icm-") as folder:
            return await asyncio.wait_for(
                _connected(usb[0].serial, Path(folder), selector), timeouts.READ
            )
    except (TimeoutError, asyncio.CancelledError):
        raise DeviceError("Ожидание USB/lockdown истекло или операция отменена.") from None
    except DeviceError:
        raise
    except Exception as error:
        from pymobiledevice3.exceptions import (
            ConnectionFailedToUsbmuxdError,
            PasscodeRequiredError,
            PasswordRequiredError,
        )

        if isinstance(error, ConnectionFailedToUsbmuxdError):
            message = (
                "USB-доступ через компоненты Apple недоступен. "
                "Проверьте iTunes или Apple Devices."
            )
        elif isinstance(error, (PasscodeRequiredError, PasswordRequiredError)):
            message = "iPhone требует разблокировки кодом; разблокируйте его и повторите команду."
        else:
            message = "Не удалось прочитать iPhone: USB-сессия или API недоступны."
        raise DeviceError(message) from None
    finally:
        logging.disable(previous)
