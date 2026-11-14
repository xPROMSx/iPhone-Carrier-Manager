> **This project is in development. The program is not ready to use.**

[Русский](README.md)

# iPhone-Carrier-Manager

A planned open source tool for Windows to check, prepare, and install IPCC
carrier profiles on an iPhone over USB.

## Development status

Only the Python project skeleton, documentation, and CI are in place.
There are no working features. The architecture calls for pymobiledevice3
to communicate with the iPhone, but it is not a dependency yet.

## Features

Features will be documented once implemented.

## Requirements

To be confirmed. Windows is the target platform. The skeleton is developed with
Python 3.12; local development commands are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Installation

Instructions will be added after the first working release.

## Usage

Not available yet.

## What the program changes on your phone

The current skeleton performs no phone operations.
Before working instructions are published, this section will describe which data
changes, the limitations, and the available recovery options after a failure.

## Safety and limitations

Installing an IPCC may change the carrier profile. A complete rollback has not been
confirmed: reinstalling the previous package does not guarantee a return to factory settings.
Writing to the phone requires the owner's explicit permission and a prepared recovery path.
Warnings will appear before instructions.

5G and VoWiFi support and compatibility with iOS versions have not been tested.
There are no plans for a server, GUI, telemetry, or automatic updates.
Apple carrier files and real user data are not stored in this repository.
For vulnerability reporting, see [SECURITY.md](SECURITY.md).

## Other projects

[3x-ui-auto-nginx](https://github.com/xPROMSx/3x-ui-auto-nginx):
a tool for deploying 3x-ui and Xray with Nginx on your own VPS.

## Support development

Donations are optional and do not affect access to the source code or future features.

- [SBP / CloudTips (QR, Mir, bank app)](https://pay.cloudtips.ru/p/9f240c8b).
- [Tribute (bank card)](https://web.tribute.tg/d/S0p).
- [All donation methods](docs/SUPPORT.en.md), including cryptocurrency and QR codes.

## License

[GNU GPL v3 or later](LICENSE) (GPL-3.0-or-later).
For library notices, see [NOTICE](NOTICE).
