# V2 development environment

V2 development uses an isolated Python 3.13.14 environment and a frontend-local Node.js
22.17.1 installation. Never point V2 `node_modules` at the visual prototype or reuse the V1
virtual environment.

## First-time bootstrap

From the repository root:

```bash
v2/scripts/bootstrap-dev.sh
```

The script installs a project-local uv-managed Python under `.tools/`, creates
`v2/backend/.venv`, installs the exact backend requirements, and performs `npm ci` in
`v2/frontend`. It refuses to overwrite a symbolic-link `node_modules` directory.

## Repeatable checks

```bash
v2/scripts/check.sh
```

Set `V2_PYTHON=/absolute/path/to/python` only for an intentional compatibility run. The default
must be `v2/backend/.venv/bin/python` and must report Python 3.13.14, Flask 3.1.3, and Waitress
3.0.2. The gate runs pinned Ruff and ESLint checks before the unit and production-build suites.
Python developer-only tools live in `v2/backend/requirements-dev.txt`; frontend build and lint
tools remain in `devDependencies` and do not enter the shipped production-dependency SBOM.

Generated builds, QA screenshots, candidate packages, databases, logs, backups, secrets, and
runtime trees belong under ignored locations such as `v2/out/`; they are not source files.
Visual audit summaries may be committed, while full screenshot runs are CI/local artifacts.

Automated checks do not replace Windows 10/11 ordinary-user installation, UAC, DACL, scheduled
task, firewall, reboot, backup/restore, LAN-client, signing, SmartScreen, or EDR acceptance.
