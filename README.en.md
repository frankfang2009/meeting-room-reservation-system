# Meeting Room Reservation System

[简体中文](README.md) · [Contributing](CONTRIBUTING.md) · [Security](.github/SECURITY.md) · [Apache-2.0](LICENSE)

[![CI](https://github.com/frankfang2009/meeting-room-reservation-system/actions/workflows/ci.yml/badge.svg)](https://github.com/frankfang2009/meeting-room-reservation-system/actions/workflows/ci.yml) [![CodeQL](https://github.com/frankfang2009/meeting-room-reservation-system/actions/workflows/codeql.yml/badge.svg)](https://github.com/frankfang2009/meeting-room-reservation-system/actions/workflows/codeql.yml)

A self-hosted meeting and interview room reservation system for trusted local networks. The current V2.5.0 line uses React, Flask, and SQLite and includes a shared calendar, reservation workflows and handovers, a Data Center with scoped CSV exports, a privacy-minimized public display, administration, backup and recovery, a complete offline help center, Windows fresh-install/offline-update tooling, and a downloadable macOS self-host edition.

> This repository publishes source code, not a production Windows installer. V2.5.0 Windows installers and update packages target 64-bit Windows 10/11 on x86-64 (AMD64); Windows ARM64 and 32-bit systems are outside the current support matrix. They have not completed ordinary-user physical acceptance or Authenticode signing, so automated Windows artifacts are internal candidates only. The macOS arm64 self-host edition is formally distributed through [GitHub Releases](https://github.com/frankfang2009/meeting-room-reservation-system/releases) (unsigned; first launch requires macOS' per-app approval flow).

## Highlights

- Organization-wide calendar visibility with owner-scoped employee mutations and administrator controls.
- Revision-based concurrency protection, slot conflict checks, and transactional reservation/slot/event writes.
- History, in-browser reminders, global and personal tags, and manual copying of reminder text.
- Owner-confirmed handovers plus immediate administrator assignment, with event history and explicit notifications.
- A role-scoped Data Center and detailed CSV export: employees are restricted to themselves; administrators can select organization-wide or individual views.
- A server-side allowlisted, masked public display that excludes case numbers, notes, tags, and staff identity.
- Loopback-only first-run setup before the service is reopened on a trusted LAN.
- Installation identity checks, diagnostics, daily backups, atomic recovery, and fail-closed database handling.
- A 55-article, nine-category offline help center available before and after sign-in.

## Security and deployment boundary

The product targets trusted LANs and currently uses HTTP (Windows edition is limited to Domain/Private LANs; the macOS self-host edition is intended for single-machine use). Do not expose it directly to the Internet, guest networks, or untrusted Wi-Fi. Never upload production/customer databases, logs, backups, secrets, or personal data to this repository or an issue.

Read the [deployment security guide](v2/docs/SECURITY-DEPLOYMENT.md) and [security policy](.github/SECURITY.md) before deployment.

## macOS self-host edition

Apple Silicon users on macOS 13 or later do not need to install Python or Node.js:

1. Download `meeting-room-v2-<version>-macOS-arm64.dmg` from [Releases](https://github.com/frankfang2009/meeting-room-reservation-system/releases) and verify its SHA-256 against the release notes.
2. Mount the DMG, copy `会议室预约系统V2-macOS` to Applications or another writable location, then eject the DMG. Do not run from the mounted image.
3. On first launch, right-click `启动.command`, choose Open, and approve this app once. Do not disable Gatekeeper.
4. Complete first-run setup at `http://127.0.0.1:8080`. Application data stays under the portable folder's `data/`, `backups/`, and `logs/` directories.

The administrator update check only reports a newer GitHub Release and links to it; it never downloads or installs updates automatically.

## Development quick start

The complete local gate pins Python 3.13.14, Node.js 22.17.1, npm, and [uv](https://docs.astral.sh/uv/); direct backend dependencies are Flask 3.1.3 and Waitress 3.0.2. Production packages include the frozen runtime, so end users do not install Python or Node.js separately.

```bash
v2/scripts/bootstrap-dev.sh
v2/scripts/check.sh
```

Run the frontend development server:

```bash
cd v2/frontend
npm run dev
```

After building the frontend, start the local first-run backend:

```bash
cd v2/frontend && npm run build
cd ../backend && .venv/bin/python server.py
```

A new database is created as V2 generation 2 with `setup_complete=0`; the service listens only on `127.0.0.1:8080` until setup completes. Use isolated development data only.

## Repository layout

- `v2/frontend/`: production React/Vite client.
- `v2/backend/`: Flask API, authentication, SQLite data, backups, and service runtime.
- `v2/installer/`: Windows fresh-install/offline-update candidate tooling and macOS self-host packaging.
- `v2/docs/`: product, API, architecture, security, and release contracts.
- `02_开发工作区/`: read-only V1 maintenance history; no new product features.

V2 is a fresh product generation. It never reads, migrates, modifies, or deletes V1 data.

## Contributing and license

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before starting. Changes to permissions, data boundaries, the public display, or installation/recovery behavior must update the relevant contracts and add regression tests.

Licensed under the [Apache License 2.0](LICENSE). Third-party components remain under their respective licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
