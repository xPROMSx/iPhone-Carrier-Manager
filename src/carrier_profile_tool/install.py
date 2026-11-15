"""Одна установка, одна перезагрузка и проверка чтением. Секреты только в памяти."""

import asyncio
import logging
import sys
import tempfile
import textwrap
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


def same_lines(before, after):
    """Снимок полный и содержит ровно те же линии, что были до установки."""
    if after.line_problem or not after.lines:
        return False
    keys = lambda snapshot: {  # noqa: E731
        (line.slot, line.integrated_circuit_card_identity) for line in snapshot.lines
    }
    return len(keys(after)) == len(after.lines) and keys(before) == keys(after)


async def wait_for_readback(serial, cache, before=None):
    """Читает телефон после перезагрузки, пока список линий не станет полным.

    Сразу после включения iPhone может отдать неполный список линий: SIM ещё не
    зарегистрировалась. Такое чтение не считается результатом, чтение только
    повторяется (на телефон ничего не пишется). Если до конца времени полного
    списка так и нет, ошибка говорит об этом прямо.
    """
    from pymobiledevice3.exceptions import PasscodeRequiredError, PasswordRequiredError

    deadline = monotonic() + timeouts.READBACK
    next_notice = 0
    incomplete = False
    while monotonic() < deadline:
        try:
            snapshot = await asyncio.wait_for(
                read_same(serial, cache), min(timeouts.READ, deadline - monotonic())
            )
            if before is None or same_lines(before, snapshot):
                return snapshot
            incomplete = True
        except (PasscodeRequiredError, PasswordRequiredError):
            if monotonic() >= next_notice:
                print("разблокируйте iPhone")
                next_notice = monotonic() + timeouts.UNLOCK_NOTICE
        except Exception:
            pass
        await pause(min(timeouts.POLL, max(0, deadline - monotonic())))
    if incomplete:
        raise device.DeviceError(
            "состав линий изменился или телефон не отдал полный список за "
            f"{timeouts.READBACK} с"
        )
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


def show_profiles(snapshot, plain=False):
    from carrier_profile_tool.cli import profile, safe

    for line in snapshot.lines:
        if plain:
            plain_text(f"Линия slot:{safe(line.slot)}: "
                       f"{safe(line.identifier).removeprefix('com.apple.')}, "
                       f"версия {safe(line.version)}")
        else:
            print(f"Линия slot:{safe(line.slot)}")
            profile("Прочитанный профиль", line.identifier, line.version)
    if snapshot.line_problem:
        print("Линии определены неполно или неоднозначно.")


def plain_text(text, indent=""):
    print(textwrap.fill(text, width=78, initial_indent=indent, subsequent_indent=indent))


def confirm(before, selector, package, rollback, plain=False, previous=None,
            tested=False, from_database=False):
    from carrier_profile_tool.cli import profile, safe

    line = device.choose_line(before, selector)
    if plain:
        plain_text(f"Линия:             slot:{safe(line.slot)}")
        plain_text(f"Сейчас на линии:   {safe(line.identifier).removeprefix('com.apple.')}, "
                   f"версия {safe(line.version)}")
        plain_text(f"Будет установлен:  {safe(package.identifier).removeprefix('com.apple.')}, "
                   f"версия {safe(package.version)}")
        label = ("Прежний профиль подготовлен для повторной установки"
                 if rollback == "точный возврат" else
                 "Прежний профиль подготовлен (версия отличается от текущей, "
                 "возврат приблизительный)")
        plain_text(f"{label}: {safe(previous.identifier).removeprefix('com.apple.')}, "
                   f"версия {safe(previous.version)}")
        print("\nЧто произойдёт:")
        for text in (
            "профиль будет записан на выбранную линию, другие линии программа не трогает;",
            "iPhone перезагрузится;",
            "программа прочитает настройки телефона и покажет результат.",
        ):
            print(textwrap.fill("- " + text, width=78, initial_indent="  ",
                                subsequent_indent="    "))
        print("\nЧто важно знать:")
        facts = ["повторная установка прежнего профиля может не вернуть все настройки;",
                 "программа ничего не удаляет;"]
        if not tested:
            facts.append("тесты проводились выборочно на узком круге моделей, все действия "
                         "вы выполняете на свой страх и риск.")
        for text in facts:
            print(textwrap.fill("- " + text, width=78, initial_indent="  ",
                                subsequent_indent="    "))
        print()
        plain_text("Файлы профиля проверены по контрольным суммам из базы." if from_database
                   else "Свой файл: подлинность не проверялась.")
        if before.ios != "27.2.1":
            plain_text(f"Версия iOS на телефоне ({safe(before.ios)}) отличается от той, "
                       "на которой проверялась программа.")
        print()
        plain_text("Не отключайте кабель и не трогайте телефон до конца. "
                   "После перезагрузки разблокируйте его.")
        print()
        return confirm_input()
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
    return confirm_input()


def confirm_input():
    if not sys.stdin.isatty():
        raise device.DeviceError("Подтверждение требует терминала (TTY).")
    try:
        answer = input("Для продолжения введите ровно INSTALL: ")
    except EOFError:
        answer = ""
    if answer != "INSTALL":
        raise device.DeviceError("Установка отменена: подтверждение не получено.")


def finish(reasons, selector, plain=False, readback=None):
    from carrier_profile_tool.cli import safe

    if plain:
        print("\nРезультат")
        print("-" * 62)
        if readback is not None:
            show_profiles(readback, plain=True)
            print()
        if reasons:
            plain_text("НЕ ПОДТВЕРЖДЕНО: " + "; ".join(reasons))
            plain_text("Результат не подтверждён. Проверьте настройки телефона. Не запускайте "
                       "установку повторно, пока не убедитесь в состоянии телефона.")
        else:
            print("ПОДТВЕРЖДЕНО (перезагрузка подтверждена косвенно)")
            plain_text("Профиль установлен, iPhone перезагружен, настройки прочитаны.")
        print()
        plain_text("Чтобы поставить прежний профиль снова, запустите программу, выберите ту же "
                   "линию и этот профиль в списке (если его там нет, укажите свой файл).")
        return 4 if reasons else 0
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


async def installation_failure_readback(serial, cache, plain=False):
    try:
        snapshot = await asyncio.wait_for(read_same(serial, cache), timeouts.READ)
        show_profiles(snapshot, plain=True) if plain else show_profiles(snapshot)
    except (Exception, asyncio.CancelledError, KeyboardInterrupt):
        print("Однократное чтение телефона не выполнено.")


async def run_install(source, selector, previous, cache, plain=False, tested=False,
                      from_database=False, readback=None):
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
        if plain:
            confirm(before, selector, source, rollback, plain=True, previous=previous,
                    tested=tested, from_database=from_database)
        else:
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
            if plain:
                await installation_failure_readback(serial, cache, plain=True)
            else:
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
            after = await wait_for_readback(serial, cache, before)
        except device.DeviceError as error:
            reasons.append(str(error))
        else:
            if plain:
                readback.append(after)
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
    return await install_packages(source, previous, selector)


async def install_packages(source, previous, selector, plain=False, tested=False,
                           from_database=False):
    disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    result = None
    readback = []
    try:
        existing = Path.home() / ".pymobiledevice3"
        context = (
            nullcontext(existing)
            if existing.is_dir()
            else tempfile.TemporaryDirectory(prefix="icm-")
        )
        with context as folder:
            if plain:
                result = await run_install(source, selector, previous, Path(folder), plain=True,
                                           tested=tested, from_database=from_database,
                                           readback=readback)
            else:
                result = await run_install(source, selector, previous, Path(folder))
    except (Exception, asyncio.CancelledError, KeyboardInterrupt):
        if result is None:
            raise
        result.append("не удалось завершить очистку временного каталога")
    finally:
        logging.disable(disabled)
    return (finish(result, selector, plain=True, readback=readback[0] if readback else None)
            if plain else finish(result, selector))
