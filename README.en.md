# Meeting Room Reservation System

[简体中文](README.md) · [Contributing](CONTRIBUTING.md) · [Security](.github/SECURITY.md) · [Apache-2.0](LICENSE)

A self-hosted meeting and interview room reservation system for trusted local networks. The current V2.1.0 line uses React, Flask, and SQLite and includes a shared calendar, reservation workflows, a privacy-minimized public display, administration, backup and recovery, and a Windows fresh-install pipeline.

> This repository publishes source code, not a production Windows installer. V2.1.0 has not completed ordinary-user Windows 10/11 acceptance or Authenticode signing. Automated artifacts are internal candidates only and are not formal releases.

## Highlights

- Organization-wide calendar visibility with owner-scoped employee mutations and administrator controls.
- Revision-based concurrency protection, slot conflict checks, and transactional reservation/slot/event writes.
- History, in-browser reminders, global and personal tags, and manual copying of reminder text.
- A server-side allowlisted, masked public display that excludes case numbers, notes, tags, and staff identity.
- Loopback-only first-run setup before the service is reopened on a trusted LAN.
- Installation identity checks, diagnostics, daily backups, atomic recovery, and fail-closed database handling.

## Security and deployment boundary

V2.1.0 is intended only for trusted Windows Domain/Private LANs and currently uses HTTP. Do not expose it directly to the Internet, guest networks, or untrusted Wi-Fi. Never upload production/customer databases, logs, backups, secrets, or personal data to this repository or an issue.

Read the [deployment security guide](v2/docs/SECURITY-DEPLOYMENT.md) and [security policy](.github/SECURITY.md) before deployment.

## Development quick start

The complete local gate pins Python 3.13.14, Node.js 22.17.1, npm, and [uv](https://docs.astral.sh/uv/).

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
- `v2/installer/`: fresh-install and reproducible candidate tooling.
- `v2/docs/`: product, API, architecture, security, and release contracts.
- `02_开发工作区/`: read-only V1 maintenance history; no new product features.

V2 is a fresh product generation. It never reads, migrates, modifies, or deletes V1 data.

## Contributing and license

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before starting. Changes to permissions, data boundaries, the public display, or installation/recovery behavior must update the relevant contracts and add regression tests.

Licensed under the [Apache License 2.0](LICENSE). Third-party components remain under their respective licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
