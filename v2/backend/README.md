# V2 Flask backend

Production and the complete local gate target Python 3.13.14 with the exact
direct dependencies from `requirements.txt`: Flask 3.1.3 and Waitress 3.0.2.
Development must use the repository-managed isolated environment created by
`v2/scripts/bootstrap-dev.sh`; older system Python/Flask installations are not
part of the supported or tested runtime matrix.

After `v2/scripts/bootstrap-dev.sh`, run the development service from this
directory with `.venv/bin/python server.py`; do not substitute an arbitrary
system Python. A new database is created as V2 generation 2 with
`setup_complete=0`; the server binds only to `127.0.0.1` until the atomic setup
request succeeds. It then recreates Waitress on `0.0.0.0`. Existing V1 or
unidentified databases are rejected before installation identity files are
created.

The client obtains a session/CSRF token from `GET /api/v1/session`. Every JSON
write uses the same-origin session cookie and `X-CSRF-Token`.

Production packages use `_程序文件/app/service.py`; `data`, `backups`, `logs`
and `runtime` are siblings of `app` under `_程序文件`:

- no arguments starts the foreground/scheduled-task service;
- `--check` accepts only the loopback `/healthz` for the same V2 `install_id`;
- `--stop` uses `data/service.pid` plus an authenticated loopback control
  request and never scans or kills a process by port/name;
- `MEETING_ROOM_OPEN_BROWSER=1` opens the local page after startup;
- exit code `0` means success/already in the requested state, `1` means an
  identity/runtime/health failure, and `2` means invalid CLI arguments.

The installed React build is read from `_程序文件/app/static`; development
`server.py` defaults to `v2/frontend/dist/client`. Startup validates database
generation, `quick_check`, foreign keys and the setup mirror. A completed
installation with a missing, empty, damaged or rolled-back database starts in
loopback recovery mode and never initializes a replacement database.

Maintenance entry points use the same protected `data/maintenance.lock`:

- `backup.py --scheduled --expected-install-id UUID` is the daily task;
- `backup.py --catch-up --expected-install-id UUID` is launched idempotently
  after service startup and after first setup switches the listener;
- `restore.py --backup ABSOLUTE_PATH --expected-install-id UUID` accepts only a
  verified backup and sidecar from this installation's protected backup
  directory while the service is stopped. It snapshots the current database,
  atomically replaces and rechecks it, and rolls back on failure.

Maintenance commands return `0` on success/idempotent skip and `1` on failure.
