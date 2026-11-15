"""Одна установка, одна перезагрузка и проверка чтением. Секреты только в памяти."""

import asyncio
import logging
import sys
import tempfile
from contextlib import nullcontext
from enum import Enum, auto
from pathlib import Path
from time import monotonic

from carrier_profile_tool import device, timeouts
from carrier_profile_tool.build import prepare
from carrier_profile_tool.ipcc import check_file

pause = asyncio.sleep


class USBState(Enum):
    PRESENT = auto()
    ABSENT = auto()
    ERROR = auto()


def classify_previous(package, line):
    if package.identifier != line.identifier:
        raise device.DeviceError("нет пакета для возврата к текущему профилю")
    return (
        "точный возврат"
        if package.version == line.version
        else "приблизительный возврат, версия другая"
    )


async def close_quietly(service):
    if service is not None:
        try:
            await asyncio.wait_for(service.close(), timeouts.CLOSE)
        except (Exception, asyncio.CancelledError, KeyboardInterrupt):
            # After restart a disconnected socket is expected; never print its details.
            pass


async def usb_devices():
    from pymobiledevice3.usbmux import list_devices

    result = await asyncio.wait_for(list_devices(usbmux_address=device.ENDPOINT), timeouts.USB)
    return [item for item in result if item.is_usb]


async def probe_usb(serial):
    try:
        return (
            USBState.PRESENT
            if any(item.serial == serial for item in await usb_devices())
            else USBState.ABSENT
        )
    except Exception:
        return USBState.ERROR


async def fresh_snapshot(client):
    await client.get_value(domain=None, key=None)
    return await device._snapshot(client)


async def checked_snapshot(client, selector):
    first = await device._snapshot(client)
    selected = device.choose_line(first, selector)
    second = await fresh_snapshot(client)
    if selected != device.choose_line(second, selector) or (
        first.product,
        first.ios,
        first.build,
    ) != (second.product, second.ios, second.build):
        raise device.DeviceError("Данные выбранной линии изменились; установка отменена.")
    return second


async def read_same(serial, cache):
    # This helper is one read attempt; it never selects a different phone.
    usb = await usb_devices()
    if len(usb) != 1 or usb[0].serial != serial:
        raise device.DeviceError("Исходный iPhone недоступен или выбор USB неоднозначен.")
    client = None
    try:
        client = await device.open_client(serial, cache)
        return await fresh_snapshot(client)
    finally:
        await close_quietly(client)


async def install_once(client, data):
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    async with InstallationProxyService(client) as service:
        await service.install_from_bytes(data)


async def restart_once(client):
    from pymobiledevice3.services.diagnostics import DiagnosticsService

    service = DiagnosticsService(client)

    async def request():
        await service.__aenter__()
        await service.restart()

    try:
        await asyncio.wait_for(request(), timeouts.RESTART)
    finally:
        await close_quietly(service)


async def wait_for_detach(serial):
    deadline = monotonic() + timeouts.DETACH
    while monotonic() < deadline:
        state = await asyncio.wait_for(probe_usb(serial), deadline - monotonic())
        if state is USBState.ABSENT:
            return True
        await pause(min(timeouts.POLL, max(0, deadline - monotonic())))
    return False


async def wait_for_readback(serial, cache):
    from pymobiledevice3.exceptions import PasscodeRequiredError, PasswordRequiredError

    deadline = monotonic() + timeouts.READBACK
    next_notice = 0
    while monotonic() < deadline:
        try:
            return await asyncio.wait_for(
                read_same(serial, cache), min(timeouts.READ, deadline - monotonic())
            )
        except (PasscodeRequiredError, PasswordRequiredError):
            if monotonic() >= next_notice:
                print("разблокируйте iPhone")
                next_notice = monotonic() + timeouts.UNLOCK_NOTICE
        except Exception:
            pass
        await pause(min(timeouts.POLL, max(0, deadline - monotonic())))
    raise device.DeviceError("телефон не вернулся или не удалось прочитать его за 300 с")


def verify_result(before, after, selector, package):
    def keyed(snapshot):
        return {(line.slot, line.integrated_circuit_card_identity): line for line in snapshot.lines}

    old, new = keyed(before), keyed(after)
    if (
        before.line_problem
        or after.line_problem
        or len(new) != len(after.lines)
        or old.keys() != new.keys()
    ):
        return ["состав линий изменился, проверьте телефон"]
    target = device.choose_line(before, selector)
    key = (target.slot, target.integrated_circuit_card_identity)
    actual = new[key]
    reasons = []
    if actual.identifier != package.identifier:
        reasons.append("целевой профиль не совпал")
    elif actual.version != package.version:
        reasons.append("другая версия того же профиля")
    if any(new[key] != line for key, line in old.items() if line.slot != target.slot):
        print("ВНИМАНИЕ: другая линия изменилась, проверьте телефон")
        reasons.append("другая линия изменилась")
    return reasons


def show_profiles(snapshot):
    from carrier_profile_tool.cli import profile, safe

    for line in snapshot.lines:
        print(f"Линия slot:{safe(line.slot)}")
        profile("Прочитанный профиль", line.identifier, line.version)
    if snapshot.line_problem:
        print("Линии определены неполно или неоднозначно.")


def confirm(before, selector, package, rollback):
    from carrier_profile_tool.cli import profile, safe

    line = device.choose_line(before, selector)
    print(f"Модель: {safe(before.product)}; iOS: {safe(before.ios)}")
    print(f"Линия: slot:{safe(line.slot)}")
    profile("Текущий профиль", line.identifier, line.version)
    profile("Целевой профиль", package.identifier, package.version)
    print(f"Пакет для возврата: {rollback}")
    print("Подпись: UNVERIFIED; криптографическая проверка не выполнена.")
    print(
        "Маска внешнего имени: абонентский идентификатор: "
        f"{len(line.international_mobile_subscriber_identity)} цифр, идентификатор SIM: "
        f"{len(line.integrated_circuit_card_identity)} цифр"
    )
    if before.ios != "27.2.1":
        print("Предупреждение: iOS отличается от 27.2.1; совместимость не проверена.")
    print("Профиль будет записан на телефон и телефон перезагрузится.")
    print(
        "Полный возврат к прежнему состоянию не гарантирован. Возврат означает повторную "
        "установку пакета --previous и тоже не проверен."
    )
    print("Программа не удаляет профили.")
    print(
        "Не отключайте кабель и не трогайте телефон до конца. После перезагрузки разблокируйте его."
    )
    if not sys.stdin.isatty():
        raise device.DeviceError("Подтверждение требует терминала (TTY).")
    try:
        answer = input("Для продолжения введите ровно INSTALL: ")
    except EOFError:
        answer = ""
    if answer != "INSTALL":
        raise device.DeviceError("Установка отменена: подтверждение не получено.")


def finish(reasons, selector):
    from carrier_profile_tool.cli import safe

    if reasons:
        print("НЕ ПОДТВЕРЖДЕНО: " + "; ".join(reasons))
    else:
        print("ПОДТВЕРЖДЕНО (перезагрузка подтверждена косвенно)")
    print(
        "Для возврата повторно установите прежний пакет (возврат не гарантирован):\n"
        "carrier-profile-tool install --source <прежний-IPCC> "
        f"--line {safe(selector)} --previous <устанавливаемый-сейчас-IPCC>"
    )
    return 4 if reasons else 0


async def installation_failure_readback(serial, cache):
    try:
        show_profiles(await asyncio.wait_for(read_same(serial, cache), timeouts.READ))
    except (Exception, asyncio.CancelledError, KeyboardInterrupt):
        print("Однократное чтение телефона не выполнено.")


async def run_install(source, selector, previous, cache):
    from pymobiledevice3.exceptions import AppInstallError

    client = None
    started = False
    try:
        usb = await usb_devices()
        if len(usb) != 1:
            raise device.DeviceError("Нужно ровно одно USB-устройство.")
        serial = usb[0].serial
        client = await asyncio.wait_for(device.open_client(serial, cache), timeouts.READ)
        before = await asyncio.wait_for(checked_snapshot(client, selector), timeouts.READ)
        line = device.choose_line(before, selector)
        rollback = classify_previous(previous, line)
        data = prepare(source, line)
        confirm(before, selector, source, rollback)
        latest = await asyncio.wait_for(fresh_snapshot(client), timeouts.READ)
        if latest != before:
            raise device.DeviceError("Данные телефона изменились; установка отменена.")
        started = True
        try:
            await asyncio.wait_for(install_once(client, data), timeouts.INSTALL)
        except (Exception, asyncio.CancelledError, KeyboardInterrupt) as error:
            if isinstance(error, AppInstallError):
                reason = (
                    "телефон запретил установку, возможная причина ограничения "
                    "Экранного времени или управление устройством (MDM)"
                    if "InstallProhibited" in str(error)
                    else "телефон отказал в установке"
                )
            else:
                reason = "результат установки неизвестен"
            await close_quietly(client)
            client = None
            await installation_failure_readback(serial, cache)
            return [reason]
        try:
            await restart_once(client)
        except Exception:
            return ["ошибка перезагрузки; перезагрузите телефон вручную и выполните info"]
        await close_quietly(client)
        client = None
        try:
            detached = await wait_for_detach(serial)
        except TimeoutError:
            detached = False
        reasons = [] if detached else ["перезагрузка не обнаружена"]
        try:
            after = await wait_for_readback(serial, cache)
        except device.DeviceError:
            reasons.append("телефон не вернулся или не удалось прочитать его за 300 с")
        else:
            show_profiles(after)
            reasons.extend(verify_result(before, after, selector, source))
        return reasons
    except (Exception, asyncio.CancelledError, KeyboardInterrupt):
        if not started:
            raise
        return ["проверка результата прервана или недоступна"]
    finally:
        await close_quietly(client)


async def install_file(path, selector, previous_path):
    source = check_file(path)
    previous = check_file(previous_path)
    disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    result = None
    try:
        existing = Path.home() / ".pymobiledevice3"
        context = (
            nullcontext(existing)
            if existing.is_dir()
            else tempfile.TemporaryDirectory(prefix="icm-")
        )
        with context as folder:
            result = await run_install(source, selector, previous, Path(folder))
    except (Exception, asyncio.CancelledError, KeyboardInterrupt):
        if result is None:
            raise
        result.append("не удалось завершить очистку временного каталога")
    finally:
        logging.disable(disabled)
    return finish(result, selector)
