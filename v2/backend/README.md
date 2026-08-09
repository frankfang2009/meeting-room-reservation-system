# V2 Flask backend

Production targets the frozen Windows Python 3.13 runtime with exact direct
dependencies from `requirements.txt` (`Flask==3.1.3`, `waitress==3.0.2`). The
code is also covered by compatibility tests under the repository's local
Python 3.8 / Flask 3.0.3 environment.

Run the service from this directory with `python server.py`. A new database is
created as V2 generation 2 with `setup_complete=0`; the server binds only to
`127.0.0.1` until the atomic setup request succeeds. It then recreates Waitress
on `0.0.0.0`. Existing V1 or unidentified databases are rejected before
installation identity files are created.

The client obtains a session/CSRF token from `GET /api/v1/session`. Every JSON
write uses the same-origin session cookie and `X-CSRF-Token`.

Production packages use `_程序文件/service.py`:

- no arguments starts the foreground/scheduled-task service;
- `--check` accepts only the loopback `/healthz` for the same V2 `install_id`;
- `--stop` uses `data/service.pid` plus an authenticated loopback control
  request and never scans or kills a process by port/name;
- `MEETING_ROOM_OPEN_BROWSER=1` opens the local page after startup;
- exit code `0` means success/already in the requested state, `1` means an
  identity/runtime/health failure, and `2` means invalid CLI arguments.

The installed React build is read from `_程序文件/static`; development
`server.py` defaults to `v2/frontend/dist/client`.
