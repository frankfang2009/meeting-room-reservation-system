"""One-off acceptance evidence for the two immutable macOS release images."""
import contextlib
import datetime
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request


ASSETS = {
    ("2.5.0", "dmg"): "b00679ebd990cd741fb7650bfd810f406725ac0408082b4b4b1ba72b24dd2094",
    ("2.5.1", "dmg"): "f2c0024eaf05226c640be2d9e301e97375294f4557e4c04bc5bae0b8ebdb4895",
    ("2.5.1", "zip"): "1d256d686a77463c0b95dea2b8b6b3f32bc7c2141df21faedddf2213d1b64853",
}
REPO = "https://github.com/frankfang2009/meeting-room-reservation-system"
HERE = Path(__file__).resolve().parents[3]
APP = "会议室预约系统V2-macOS"
DATA = ("data", "backups", "logs")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, timeout=300, **kwargs)


def download(root, version, ext):
    asset = f"meeting-room-v2-{version}-macOS-arm64.{ext}"
    path = root / f"会议室预约系统-V{version}-macOS-arm64.{ext}"
    run("curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3",
        f"{REPO}/releases/download/v{version}/{asset}", "--output", path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != ASSETS[version, ext]:
        raise ValueError("release checksum mismatch")
    return path


def install(root, image, dest):
    mount = root / "image"
    mount.mkdir(exist_ok=True)
    run("hdiutil", "attach", image, "-readonly", "-nobrowse", "-mountpoint", mount)
    try:
        run("ditto", mount / APP, dest)
    finally:
        run("hdiutil", "detach", mount)


def until(fn):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if fn():
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise TimeoutError("release acceptance timed out")


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, path, payload=None):
        headers = {}
        body = None
        if payload is not None:
            headers = {"Content-Type": "application/json", "X-CSRF-Token": self.request("/api/v1/session")["csrfToken"]}
            body = json.dumps(payload).encode()
        req = urllib.request.Request("http://127.0.0.1:8080" + path, data=body, headers=headers)
        with self.opener.open(req, timeout=6) as response:
            return json.load(response)


@contextlib.contextmanager
def serving(folder):
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8080)) == 0:
            raise RuntimeError("test runner port already in use")
    proc = subprocess.Popen(["bash", folder / "启动.command"], cwd=folder)
    waiter = threading.Thread(target=proc.wait, daemon=True)
    waiter.start()
    client = Client()
    try:
        def ready():
            if proc.poll() is not None:
                raise RuntimeError(f"launcher exited: {proc.returncode}")
            return client.request("/healthz").get("ok")
        until(ready)
        yield client
        run("bash", folder / "停止.command")
        waiter.join(12)
        if waiter.is_alive() or (folder / "data/service.pid").exists():
            raise RuntimeError("normal stop did not finish")
    finally:
        if proc.poll() is None:
            proc.terminate()
            waiter.join(12)
        if waiter.is_alive():
            proc.kill()
            waiter.join()


def snapshot(folder):
    return {
        str(f.relative_to(folder)): hashlib.sha256(f.read_bytes()).hexdigest()
        for name in DATA for f in (folder / name).rglob("*") if f.is_file()
    }


@unittest.skipUnless(os.getenv("GITHUB_ACTIONS") == "true" and platform.system() == "Darwin",
                     "release image rehearsal needs a disposable macOS runner")
class ReleasedMacUpgrade(unittest.TestCase):
    def test_published_images(self):
        self.assertEqual(platform.machine(), "arm64")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dmg = download(root, "2.5.0", "dmg")
            new_dmg = download(root, "2.5.1", "dmg")
            new_zip = download(root, "2.5.1", "zip")
            run("bash", HERE / ".github/scripts/v2-macos-acceptance.sh", new_zip, new_dmg, root / "nine-steps")
            old, new = root / "old", root / "new"
            install(root, old_dmg, old)
            install(root, new_dmg, new)
            self.assertTrue(all(not (new / d).exists() for d in DATA))
            credentials = {"username": "rehearsal", "password": secrets.token_urlsafe(22)}
            with serving(old) as api:
                api.request("/api/v1/setup/complete", {
                    "admin": {**credentials, "name": "演练管理员", "department": "演练"},
                    "rooms": [{"name": "演练室"}], "workStart": "08:00", "workEnd": "17:00",
                })
                until(lambda: api.request("/healthz").get("bind_mode") == "lan")
                api.request("/api/v1/session", credentials)
                boot = api.request("/api/v1/bootstrap")
                self.assertEqual(boot["productVersion"].lstrip("Vv"), "2.5.0")
                room = boot["rooms"][0]["id"]
                day = (datetime.date.fromisoformat(boot["serverDate"]) + datetime.timedelta(days=2)).isoformat()
                payload = {"date": day, "roomId": room, "start": "10:00", "duration": 30,
                           "partyName": "合成演练", "purpose": "升级前记录", "tagId": "tag-1"}
                record = api.request("/api/v1/reservations", payload)
                until(lambda: any((old / "backups").glob("*.db")))
                api.request("/api/v1/admin/backups", {})
                self.assertTrue(api.request("/api/v1/admin/system")["backupCaughtUp"])
            before = snapshot(old)
            self.assertIn("data/.secret_key", before)
            self.assertTrue(any(k.startswith("backups/") and k.endswith(".db") for k in before))
            self.assertIn("logs/service.log", before)
            for d in DATA:
                run("ditto", old / d, new / d)
            self.assertEqual(snapshot(new), before)

            # Bad new package, before accepting business writes: the intact old installation can serve again.
            (new / "runtime").rename(new / "held-runtime")
            attempt = subprocess.run(["bash", new / "启动.command"], capture_output=True, timeout=15)
            self.assertNotEqual(attempt.returncode, 0)
            self.assertIn("缺少运行环境", attempt.stdout.decode())
            with serving(old) as recovered:
                recovered.request("/api/v1/session", credentials)
                self.assertEqual(recovered.request("/api/v1/reservations/" + record["id"])["partyName"], "合成演练")
            (new / "held-runtime").rename(new / "runtime")
            original_after_fallback = snapshot(old)
            # Repeat the complete copy into a fresh destination after any use of the old server.
            fresh = root / "retry"
            install(root, new_dmg, fresh)
            for d in DATA:
                run("ditto", old / d, fresh / d)
            self.assertEqual(snapshot(fresh), original_after_fallback)
            with serving(fresh) as upgraded:
                self.assertTrue(upgraded.request("/healthz")["setup_complete"])
                upgraded.request("/api/v1/session", credentials)
                new_boot = upgraded.request("/api/v1/bootstrap")
                self.assertEqual(new_boot["productVersion"].lstrip("Vv"), "2.5.1")
                self.assertEqual(boot["currentUser"]["id"], new_boot["currentUser"]["id"])
                self.assertEqual(new_boot["settings"], boot["settings"])
                self.assertEqual(new_boot["rooms"][0]["id"], room)
                read = upgraded.request("/api/v1/reservations/" + record["id"])
                self.assertEqual((read["purpose"], read["start"]), ("升级前记录", "10:00"))
                self.assertTrue(upgraded.request("/api/v1/admin/system")["backupCaughtUp"])
                new_row = upgraded.request("/api/v1/reservations", {**payload, "start": "11:00", "purpose": "升级后记录"})
            self.assertEqual(snapshot(old), original_after_fallback)
            with serving(fresh) as restarted:
                restarted.request("/api/v1/session", credentials)
                self.assertEqual(restarted.request("/api/v1/reservations/" + new_row["id"])["purpose"], "升级后记录")
            for name in ("data/install_id", "data/install.json", "data/.secret_key"):
                self.assertEqual((old/name).read_bytes(), (fresh/name).read_bytes())
            self.assertEqual(snapshot(old), original_after_fallback)
            print("MAC_RELEASE_UPGRADE_AND_FALLBACK_PASS", flush=True)

