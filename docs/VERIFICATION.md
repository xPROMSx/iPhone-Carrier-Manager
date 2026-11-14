# Источники и границы проверки v0.1

## Исходное состояние

Main: `564c7cd77a995f62ff51c1d3a14b5ac75cc6455a`.
Прочитаны AGENTS.md, CLAUDE.md, pyproject.toml, тест гигиены, workflow,
README двух языков, CONTRIBUTING.md, SECURITY.md и NOTICE.

## Наблюдение владельца

Два одинаковых запуска разовой пробы: один iPhone 17 Pro, iOS 27.2.1, Windows 11,
pymobiledevice3 11.55.0, существующее сопряжение. Две eSIM, одна включена.
Codex не обращался к устройству и не выполнял пробу. Скрипт в проект не входит.

CarrierBundleInfoArray: list из двух dict, по восемь ключей, все значения str:

```text
CFBundleIdentifier
CFBundleVersion
IntegratedCircuitCardIdentity
InternationalMobileSubscriberIdentity
MCC
MNC
Slot
kCTPostponementInfoAvailable
```

Slot: `kOne`/`kTwo`. Длина абонентского идентификатора 15, идентификаторов SIM 19 и 20.
MNC сохранял ведущий ноль; GID1 отсутствовал. Владелец подтвердил соответствие kOne
экрану; вторую запись сверил только по данным телефона, поскольку на экране она
не отображалась. Идентификаторы и их фрагменты здесь не записываются.

Смысл Slot, активность по этому полю и полнота списка не доказаны. Наблюдение не
обобщается на другие устройства/iOS. Владелец снял блокер схемы и разрешил пробный
build. Это не проверка готового приложения.

## Статусы

- A: точные подписываемые байты CBSignature3 — НЕ ПОДТВЕРЖДЕНО.
- B: происхождение доверенного ключа — НЕ ПОДТВЕРЖДЕНО.
- C: собственная проверка официального пакета — НЕ ПОДТВЕРЖДЕНО.
- D: блокер схемы СНЯТ ВЛАДЕЛЬЦЕМ в пределах наблюдения выше.
- E: отсутствие постоянного хранения идентификаторов проверяется реализацией;
  физическое уничтожение памяти/swap и аварийных остатков не подтверждается.

Криптографии в v0.1 нет, статус UNVERIFIED. Наблюдаемое переименование bundle
не объявляется универсально сохраняющим применимость подписей. Для v0.2 нужны
точные байты каждой группы, алгоритм/кодирование, проверяемое происхождение ключа,
положительные проверки официальных пакетов и отрицательные проверки изменений.
Математический успех неизвестным ключом не равен VERIFIED. VERIFIED не означает
пригодность установки.

## Библиотека и конфиденциальность

pymobiledevice3 11.55.0, исходный SHA `b08cee88ecd1d7afebc59882563c98addad8bc9e`.
SHA-256 wheel: `b85b9589778679b43472d06ead7052364a6b06d43383b7d4ccbe390fcbafa5a1`.
Основные изученные файлы wheel совпали с исходниками. Реальные IPCC не получались.

autopair=False запрещает новое сопряжение, но проверяет существующее и открывает
сессию. Фабрика читает полный GetValue и перечитывает его после открытия сессии.
Pairing record ищется в usbmuxd, системном Apple Lockdown, затем локальном каталоге.
Переданный каталог меняет только последний источник. Временный каталог применяется
лишь при отсутствии штатного локального каталога.

_forget_pair_record вызывается из unpair и может удалять записи локально/usbmuxd.
Приложение этот путь не вызывает. Ошибка TLS может сбросить запись только в памяти.
Фабрика создаёт соединение до try и ловит Exception. CancelledError до возврата
клиента не гарантирует закрытия внутреннего соединения до выхода процесса;
после отмены команда завершается, ОС закрывает соединение. Полученный клиент
закрывается в finally. Штатный TLS пишет существующий приватный ключ в системный
temp до try. Удаление в finally не гарантирует очистки при ошибке записи или аварии.
Diagnostics не используется: MobileGestalt документирован как недоступный с iOS 17.4.

## Источники

Дата доступа: 14.11.2026. Изменяемые страницы без отдельного SHA.

- https://pypi.org/project/pymobiledevice3/11.55.0/
- https://github.com/doronz88/pymobiledevice3/tree/b08cee88ecd1d7afebc59882563c98addad8bc9e
  (lockdown.py, usbmux.py, pair_records.py, common.py, service_connection.py, Windows utils).
- https://doronz88.github.io/pymobiledevice3/master/guides/python-api/
- https://doronz88.github.io/pymobiledevice3/master/api/connection/
- https://doronz88.github.io/pymobiledevice3/master/installation/
- https://doronz88.github.io/pymobiledevice3/master/guides/troubleshooting/
- https://doronz88.github.io/pymobiledevice3/latest/cli/diagnostics/
- https://theapplewiki.com/wiki/Carrier_Bundle
- https://carrierexplode.com/wiki/ios/carrier-bundle
- https://carrierexplode.com/wiki/ios/carrier-bundle-manifest
- https://docs.python.org/3.12/library/zipfile.html
- https://docs.python.org/3.12/library/plistlib.html
- https://www.python.org/downloads/release/python-31210/
- https://pyinstaller.org/en/stable/operating-mode.html (6.22.3)
- https://pyinstaller.org/en/stable/license.html (6.22.3)
- https://docs.github.com/en/actions/tutorials/store-and-share-data
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow

Для CI изучены actions/setup-python@v7 (`6d4fd22b20465ccc0f7354476588e8df7c9ca0e0`),
actions/python-versions (`9bc1fe8f6b4befbb1edd3955a1adc8fc086b3e08`),
actions/runner-images (`95e6f51a068507e8f43d061d2166a39d0ef36ce8`).
В манифесте были Windows 3.12.10 x64 и Ubuntu 24.04 3.12.16 x64.
Фактические micro-version, runner и транзитивные зависимости могут меняться.

## Непроверенное после CI

Готовый EXE, USB и отсутствие нового сопряжения на Windows 11 владельца, готовый
build на реальном устройстве, другие устройства/iOS, Windows 10, антивирусы,
аварийная очистка и совместимость будущей установки требуют отдельных результатов.
Тесты используют только выдуманные данные. Если появится извлечение файлов на диск,
проверки под файловую систему добавляются в той версии, не в v0.1.

## Уточнения по аудиту PR #2

CLI перенастраивает stdout/stderr на UTF-8, включая каналы и файлы. На Linux
воспроизведён UnicodeEncodeError при cp1252 до исправления.
Имена исходных файлов не выводятся; check использует порядковые номера.
Все *.plist внутри bundle проверяются на читаемость, без толкования дополнительных данных.
По уточнению владельца на проверенной Windows 11 USB работает через
AppleMobileDeviceProcess без службы Apple Mobile Device Service. Нужны работающие
компоненты Apple (iTunes или Apple Devices), конкретное имя службы не обязательно.
