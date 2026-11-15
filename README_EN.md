[Русский](README.md)

# iPhone-Carrier-Manager

v0.2 reads an already paired iPhone over USB, checks and prepares IPCC files
in memory, and installs a profile on one selected line with a reboot and readback.

## Development status

The implementation needs owner verification on Windows 11 and a real device.
The schema was observed on one iPhone 17 Pro running iOS 27.2.1 only.
Slot semantics and completeness of the eSIM list are unproven. Support for other
phones and iOS versions is not claimed. Cryptographic verification is not implemented; signatures remain UNVERIFIED.

## Features

- `info`: device information and available carrier records.
- `check`: ZIP/plist, metadata, signature-file discovery and source SHA-256.
- `build`: trial renaming of the outer bundle in memory, comparing inner files byte for byte.
- `install`: one installation, one reboot, and verification by reading the phone.

No profile removal, automatic installation/reboot retries, export, carrier catalogue,
IPCC downloads, history, GUI,
telemetry, server or automatic updates.

## Requirements

Windows 11 x64. Windows 10 is untested. The Python package requires Python >=3.12;
development and CI use 3.12. The EXE does not require a separate Python installation.

USB access needs working Apple components supplied by iTunes or Apple Devices.
Some computers use AppleMobileDeviceProcess and have no service named Apple Mobile
Device Service; its absence alone is not an error. Library documentation:
https://doronz88.github.io/pymobiledevice3/master/installation/
The program does not install drivers, modify or
start services, or request administrator privileges automatically. Use a USB cable,
an unlocked phone and an existing pairing. Establish trust normally outside the program
if needed, then retry.

## Installation

Portable build: extract the entire private ZIP and run `iphone-carrier-manager.exe`.
Keep its neighbouring files and `_internal`. The owner requests an artifact using the
`build-windows` Draft PR label; retention is 7 days. New commits do not rebuild it
automatically. Check the artifact's source SHA.

Python package from a checkout: `python -m pip install .`.
`python -m iphone_carrier_manager` provides the same commands.
Development: [CONTRIBUTING.md](CONTRIBUTING.md).

## Usage

`--help` describes arguments; `--version` prints the version. Ctrl+C stops a command.

```text
iphone-carrier-manager info
iphone-carrier-manager check source.ipcc
iphone-carrier-manager check local-packages
iphone-carrier-manager build --source source.ipcc --line slot:kOne
```

`info` needs USB and reads lockdown. The library obtains full GetValue responses,
but only allowed information is displayed. Raw responses are not saved or logged.
Line activity is not determined. Missing data is reported as an undefined line.
List completeness is not guaranteed. Nothing on the phone changes. Stop with Ctrl+C.

`check` needs no phone and does not modify the source. It checks one file or immediate
IPCC children of a directory, without recursion. Results use ordinal file numbers without printing source filenames; no report
is saved. SHA-256 describes the source file, not Apple authenticity. Stop with Ctrl+C.

`build` needs USB and one explicit IPCC. Selection matches the Slot string exactly:
`slot:kOne` means neither “SIM 1” nor the enabled line. The same record supplies
`InternationalMobileSubscriberIdentity` and `IntegratedCircuitCardIdentity`.
MNC remains a string including its leading zero. Numeric or duplicate Slot values
cause refusal. The current profile and its exact version are required. The target
profile may differ; both are shown for comparison without claiming compatibility.

Identifiers must be ASCII-digit strings: subscriber length 10–15, SIM length 18–22.
No normalization occurs. Fresh reads must agree on the selected record. The ZIP is
rebuilt in BytesIO; inner files are compared byte for byte. The result and identifiers
are not saved. The outer-name mask contains lengths only, for example subscriber
identifier: 15 digits, SIM identifier: 19 digits. No identifier values or fragments,
full outer name or prepared-ZIP hash are printed. Stop with Ctrl+C; processing ends.

**The result is not saved and was not installed on the iPhone. Successful preparation
does not establish iOS compatibility or suitability for installation, even after
future signature verification. The signature is currently unverified.**

## Installing a profile on one line

**The install command writes a carrier profile and restarts the entire iPhone.**
It may change carrier settings. Compatibility and a complete return to the previous
state are untested. The program does not remove profiles. Returning means installing
the previous package again, not restoring the phone's complete state.
UNVERIFIED means that package authenticity has not been checked cryptographically.
Keep the cable connected and leave the phone alone until the operation ends; unlock it after reboot.

Prepare both the new IPCC and a package matching the current profile for returning.
A matching identifier with a different version provides only an approximate return.
A different identifier prevents installation. Reinstalling the same package is allowed.
Each command targets one line using its exact Slot value.

```text
iphone-carrier-manager install --source new.ipcc --line slot:kOne --previous previous.ipcc
```

Before writing, the program checks both files, rereads the phone in the same session,
and displays the model, iOS, selected line, current and target profiles, return type and warnings.
iOS versions other than 27.2.1 produce an additional warning.
Continuing requires a terminal and exactly `INSTALL`, case sensitive, without surrounding spaces.
Redirected stdin is rejected. No writing occurs before this confirmation.

Installation is called once with a 120-second limit. Only Complete permits a single restart.
A service refusal, disconnect, timeout or Ctrl+C during installation prevents reboot;
the program attempts one read of the profiles. The installation result may remain unknown.
An automatic retry is not a safe continuation: read `info` and inspect the phone first.

USB detachment is awaited for up to 60 seconds, then return and readback share a 300-second limit.
Polling runs once per second. A device-list error does not count as detachment.
If a passcode is required, the program reminds you to unlock the iPhone. If detachment
was not observed, readback continues but the result remains unconfirmed.
Ctrl+C after installation starts produces an unconfirmed result; it does not undo a write.

`ПОДТВЕРЖДЕНО (перезагрузка подтверждена косвенно)` (confirmed, reboot inferred indirectly)
and exit code 0 require all of: Complete, a successful restart request, observed USB absence,
return of the same phone, the exact target identifier and version, and unchanged line membership
and other lines. Errors closing connections after successful restart do not invalidate it.
A matching profile alone is insufficient, including when reinstalling the same package.
`НЕ ПОДТВЕРЖДЕНО` (unconfirmed), a reason and exit code 4 mean insufficient confirmation,
not a proven failure. If restart fails, reboot manually and run `info`.

To return, install the previous package yourself with the files exchanged:

```text
iphone-carrier-manager install --source previous.ipcc --line slot:kOne --previous new.ipcc
```

This also requires checks and confirmation and cannot guarantee a complete return.
If the actual current profile does not match the `--previous` package, preflight rejects
the command. Do not bypass the check; inspect the phone first.

## What the program changes on your phone

`info`, `check` and `build` do not change settings, profiles, files or trust.
`install` uses AFC to stage the unpacked package in `/PublicStaging/pymobiledevice3/<uuid>.ipcc`,
then calls the CarrierBundle installation service and Diagnostics restart. The library attempts
nonrecursive removal without checking the result: files containing identifiers may remain
on the phone. The program does not clean PublicStaging. Existing pairing is used with
autopairing disabled. Wi-Fi and tunnels are not used.

On the computer, the library reads existing pairing records from usbmuxd, the system
Apple Lockdown folder, then its local folder. If `~/.pymobiledevice3` is absent, an
operation-scoped temporary folder is used and removed afterwards. It remains available through
post-reboot readback; reconnects use the same source. No new pairing records are created.
Prepared ZIP bytes and full identifiers are never written to the computer disk.

Standard TLS handling may temporarily write the existing host certificate and private
key into system temp. The library removes it in finally, but writes before try:
cleanup after write failure, crash or power loss is not guaranteed. Physical erasure
of memory, swap, crash dumps or file copies is not promised. Cancellation before the
factory returns may leave its internal connection open until process exit; the OS
closes it then. Installation/restart are never retried; only polling and reading repeat
after restart. Timeouts: usbmux 5 s; connection and reading
15 s combined, including any library reconnection.

## Safety and limitations

In v0.2 the signature is always `UNVERIFIED`: signed-data rules and key provenance
are unconfirmed. Signature files alone prove no authenticity; absence is reported.
`VERIFIED` would require complete mathematical verification with a trusted key, without
proving installation compatibility. `INVALID` means a failed supported cryptographic
check; that check is not implemented in v0.2. Unsupported formats are distinguished
from corrupt ZIP/plist files.

Exit codes: 0 success (build is trial only; install is confirmed by readback),
1 corruption/device/data error or cancellation before installation, 2 arguments,
3 incomplete check or unsupported format, 4 unconfirmed result after installation starts. Unknown structure
and multiple bundles return 3 in both check and build. A valid check with UNVERIFIED
returns 3; successful trial build with UNVERIFIED returns 0. A corrupt package prevents
an overall successful directory result.

Limits: 64 MiB source, 64 MiB total expanded data, 10,000 entries. Stored/Deflate only;
no disk extraction. All *.plist files inside the bundle are checked for readability
without interpreting additional content. Absolute paths, `..` segments, backslashes, NUL/control characters,
duplicate names, links/special entries, encryption and CRC are checked. Windows
filesystem naming checks are not performed.

Installation compatibility, 5G, VoWiFi and rollback are unconfirmed. The current profile
is not assumed to be the factory profile. Antivirus behaviour and Windows 10 are untested.
Sources/results: [VERIFICATION](docs/VERIFICATION.md).
Vulnerability reporting: [SECURITY.md](SECURITY.md).

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
