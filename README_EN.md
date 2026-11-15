<div align="center">

# 📱 Carrier Profile Tool

### Choose and install iPhone carrier profiles from your Windows PC

Connect your iPhone over USB, select a SIM or eSIM line, and choose a carrier profile. The step-by-step menu guides you through the process.

[![Windows 11](https://img.shields.io/badge/Windows-11%20%2864--bit%29-0078D4?logo=windows11&logoColor=white)](#-quick-start)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL--3.0--or--later-blue)](LICENSE)
[![Releases](https://img.shields.io/badge/Download-GitHub%20Releases-2ea44f)](https://github.com/xPROMSx/iPhone-Carrier-Manager/releases)

[🇷🇺 Русский](README.md) · [📦 Releases](https://github.com/xPROMSx/iPhone-Carrier-Manager/releases) · [🐛 Issues](https://github.com/xPROMSx/iPhone-Carrier-Manager/issues) · [❤️ Support](#-support-development)

### [📥 Download for Windows](https://github.com/xPROMSx/iPhone-Carrier-Manager/releases)

</div>

**Carrier Profile Tool** is a free Windows app for viewing and changing carrier profiles on an iPhone over USB. A carrier profile contains settings your iPhone uses to connect to a mobile network. You can inspect the current profiles, choose one from the built-in catalog, or supply your own file.

## ✨ Why Carrier Profile Tool?

| | |
| --- | --- |
| **Guided menu**<br>Follow on-screen prompts instead of typing commands. | **Profiles matched to your model**<br>The catalog shows options for the connected iPhone. |
| **Built-in downloads**<br>The app downloads catalog profiles and checks their file hashes before installation. | **One line at a time**<br>Choose which SIM or eSIM line to change on a dual-line iPhone. |
| **Review before installing**<br>See the selected line and profile before you confirm the change. | **Previous profile prepared**<br>The app prepares the previous profile package in advance so it can be installed again if needed. |

The catalog includes **MTS Russia, MegaFon, Tele2, Beeline, Vodafone Hungary, Vodafone Romania, and MTS Ukraine**. Available profiles depend on your iPhone model. The catalog covers selected models in the **iPhone 14, 15, 16, 16e, 17, Air, 17e, and 18** families.

## 🔄 How it works

1. **Connect your iPhone.** The app reads its model, iOS version, SIM/eSIM lines, and current carrier profiles.
2. **Choose a line and profile.** Pick a catalog profile or provide your own file. Catalog packages are downloaded automatically and checked against their expected hashes.
3. **Review the change.** The app shows which profile will be installed on which line. You must type **`INSTALL`** before it writes anything to the phone.
4. **Wait for verification.** The app installs the profile on the selected line, restarts the iPhone, and reads its settings again. Other lines are not installation targets.

> [!IMPORTANT]
> Installing a profile changes the carrier settings of the **selected line** and restarts the entire iPhone. Check your selection before confirming. Installing the previous profile again may not return every setting to its original state.

## 🚀 Quick start

You'll need **64-bit Windows 11**, a USB cable, internet access, and **Apple Devices or iTunes** installed.

1. Visit the [**download page**](https://github.com/xPROMSx/iPhone-Carrier-Manager/releases). Once a release is available, download the Windows archive.
2. Extract the **entire archive** into a folder and double-click the application. You don't need to install Python.
3. Connect and unlock your iPhone. Tap **Trust** if the phone asks whether to trust this computer.
4. Follow the menu prompts. You can inspect your phone without installing a profile.

Administrator privileges and jailbreaking are not required. The app does not install drivers.

If a package won't download, check your internet connection, VPN, or proxy.

## 🔒 Security and privacy

- **No accounts, ads, or telemetry.** The app doesn't collect usage analytics or send phone data to a project server.
- **No saved device identifiers.** The app doesn't write full SIM or phone identifiers to files on your computer.
- **You control changes.** No carrier profile is written to the iPhone until you confirm with `INSTALL`.
- **Open source.** The code is available to inspect in this repository.

## ⚠️ Good to know

- Installing a profile restarts the entire iPhone.
- The selected line's carrier profile changes. Mobile network behavior depends on the model, carrier, and iOS version.
- Going back means **installing the previous profile again**. This may not restore every setting to its former state.
- Testing has covered a limited selection of models, not every phone. You use the app at your own risk.
- This is an independent project and is not affiliated with Apple. Apple and iPhone are trademarks of Apple Inc.

## ❓ Frequently asked questions

**Do I need Python?**  
No. The Windows build runs directly after you extract its archive.

**Do I need administrator access or a jailbreak?**  
No. You'll need Apple Devices or iTunes components and a trusted USB connection.

**Can I check my current profile without changing anything?**  
Yes. Reading phone information doesn't install a profile.

**What if I use two eSIMs?**  
Select the intended line in the menu. The app shows your selection again before installation.

**Can I use my own profile file?**  
Yes. You can choose a local file instead of a catalog entry.

**How do I go back to my previous profile?**  
Install the previously prepared package again. This is a separate, confirmed operation, not a complete reset.

**Will this enable 5G or Wi-Fi Calling?**  
The app changes carrier profiles, but it can't promise those features. Availability depends on your iPhone, carrier, network, and iOS.

## 🌐 Other projects

[**3x-ui-auto-nginx**](https://github.com/xPROMSx/3x-ui-auto-nginx) - a tool for deploying 3x-ui and Xray with Nginx on your own VPS.

## ❤️ Support development

Donations are optional and do not affect access to the source code or future features.

- [SBP / CloudTips (QR, Mir, bank app)](https://pay.cloudtips.ru/p/9f240c8b).
- [Tribute (bank card)](https://web.tribute.tg/d/S0p).
- [All donation methods](docs/SUPPORT.en.md), including cryptocurrency and QR codes.

## 📄 License

[GNU GPL v3 or later](LICENSE) (GPL-3.0-or-later). Third-party notices: [NOTICE](NOTICE).

[For developers: technical notes (Russian)](docs/TECHNICAL.md).
