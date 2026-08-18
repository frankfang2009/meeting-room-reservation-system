from __future__ import annotations

import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import service as service_entrypoint
from tests.test_backend import BackendTestCase
from v2app.services import update_check


def _manifest(version: str = "2.3.0") -> bytes:
    return json.dumps(
        {
            "product": update_check.PRODUCT_SLUG,
            "channel": update_check.MACOS_CHANNEL,
            "version": version,
            "tag": f"v{version}",
        }
    ).encode("utf-8")


def _manifest_handler(payload: bytes, status: int) -> type:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args, **kwargs) -> None:
            return

    return Handler


class _ManifestServer:
    """极小的本地清单服务器；仅用于测试，绝不访问真实 GitHub。"""

    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self.server = http.server.HTTPServer(
            ("127.0.0.1", 0), _manifest_handler(payload, status)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://127.0.0.1:{port}/latest-macos.json"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class UpdateCheckUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name) / "data"
        self.addCleanup(self.temporary.cleanup)

    def test_parse_manifest_accepts_only_strict_schema(self) -> None:
        parsed = update_check.parse_manifest(_manifest("2.3.1"))
        self.assertEqual(parsed, {"version": "2.3.1", "tag": "v2.3.1"})
        self.assertEqual(
            update_check.parse_manifest(_manifest("2.2.3"))["version"], "2.2.3"
        )

    def test_parse_manifest_rejects_invalid_payloads(self) -> None:
        bad_payloads = [
            b"",
            b"not-json",
            b"[]",
            json.dumps({"product": "other", "channel": "macos-selfhost",
                        "version": "2.3.0", "tag": "v2.3.0"}).encode("utf-8"),
            json.dumps({"product": update_check.PRODUCT_SLUG,
                        "channel": "windows",
                        "version": "2.3.0", "tag": "v2.3.0"}).encode("utf-8"),
            json.dumps({"product": update_check.PRODUCT_SLUG,
                        "channel": update_check.MACOS_CHANNEL,
                        "version": "2.3", "tag": "v2.3"}).encode("utf-8"),
            json.dumps({"product": update_check.PRODUCT_SLUG,
                        "channel": update_check.MACOS_CHANNEL,
                        "version": "2.3.0"}).encode("utf-8"),
            json.dumps({"product": update_check.PRODUCT_SLUG,
                        "channel": update_check.MACOS_CHANNEL,
                        "version": "2.3.0", "tag": "v9.9.9"}).encode("utf-8"),
            json.dumps({"product": update_check.PRODUCT_SLUG,
                        "channel": update_check.MACOS_CHANNEL,
                        "version": 2, "tag": "v2.0.0"}).encode("utf-8"),
        ]
        for payload in bad_payloads:
            with self.assertRaises(update_check.UpdateCheckError):
                update_check.parse_manifest(payload)

    def test_version_normalization_and_release_url(self) -> None:
        self.assertEqual(update_check.normalize_version("V2.2.3"), (2, 2, 3))
        self.assertEqual(update_check.normalize_version("2.10.0"), (2, 10, 0))
        self.assertIsNone(update_check.normalize_version("2.3"))
        self.assertIsNone(update_check.normalize_version(None))
        self.assertEqual(
            update_check.release_url("v2.3.0"),
            update_check.RELEASES_INDEX_URL + "/tag/v2.3.0",
        )
        self.assertIsNone(update_check.release_url("main"))
        self.assertIsNone(update_check.release_url("https://example.com"))

    def test_perform_check_reports_available_and_persists_state(self) -> None:
        server = _ManifestServer(_manifest("9.9.9"))
        self.addCleanup(server.close)
        performed, summary = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=server.url,
            force=True,
        )
        self.assertTrue(performed)
        self.assertEqual(summary["status"], "available")
        self.assertEqual(summary["latestVersion"], "9.9.9")
        self.assertEqual(summary["currentVersion"], "2.2.3")
        self.assertIn("/releases/tag/v9.9.9", summary["releaseUrl"])
        state = json.loads(
            update_check.sidecar_path(self.data_dir).read_text(encoding="utf-8")
        )
        self.assertEqual(state["latestTag"], "v9.9.9")
        self.assertIsNotNone(state["lastSuccessAtUtc"])

    def test_perform_check_current_version_is_not_reported_as_update(self) -> None:
        server = _ManifestServer(_manifest("2.2.3"))
        self.addCleanup(server.close)
        _, summary = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=server.url,
            force=True,
        )
        self.assertEqual(summary["status"], "current")
        self.assertEqual(summary["latestVersion"], "2.2.3")

    def test_perform_check_throttles_repeated_calls(self) -> None:
        server = _ManifestServer(_manifest("9.9.9"))
        self.addCleanup(server.close)
        first, _ = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=server.url,
            force=True,
        )
        second, summary = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=server.url,
        )
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(summary["status"], "available")

    def test_perform_check_degrades_silently_on_failure(self) -> None:
        server = _ManifestServer(b"broken", status=500)
        self.addCleanup(server.close)
        performed, summary = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=server.url,
            force=True,
        )
        self.assertTrue(performed)
        self.assertEqual(summary["status"], "unknown")
        self.assertIsNone(summary["latestVersion"])

        closed = _ManifestServer(b"x")
        url = closed.url
        closed.close()
        performed, summary = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=url,
            force=True,
        )
        self.assertTrue(performed)
        self.assertEqual(summary["status"], "unknown")

    def test_oversized_manifest_is_rejected(self) -> None:
        big = _ManifestServer(b"x" * (update_check.MANIFEST_MAX_BYTES + 1))
        self.addCleanup(big.close)
        performed, summary = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=big.url,
            force=True,
        )
        self.assertTrue(performed)
        self.assertEqual(summary["status"], "unknown")

    def test_view_disabled_and_corrupt_state(self) -> None:
        self.assertEqual(
            update_check.view(
                enabled=False, data_dir=self.data_dir, current_version="2.2.3"
            ),
            {"enabled": False},
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        update_check.sidecar_path(self.data_dir).write_bytes(b"{corrupt")
        summary = update_check.view(
            enabled=True, data_dir=self.data_dir, current_version="2.2.3"
        )
        self.assertEqual(summary["status"], "unknown")

    def test_stale_current_does_not_survive_failed_recheck(self) -> None:
        # 先成功（清单等于当前版本）→ 再失败（500）：不得继续宣称“已是最新”。
        ok = _ManifestServer(_manifest("2.2.3"))
        self.addCleanup(ok.close)
        _, first = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=ok.url,
            force=True,
        )
        self.assertEqual(first["status"], "current")
        broken = _ManifestServer(b"broken", status=500)
        self.addCleanup(broken.close)
        performed, second = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=broken.url,
            force=True,
        )
        self.assertTrue(performed)
        self.assertEqual(second["status"], "unknown")
        self.assertTrue(second["lastCheckFailed"])

    def test_known_available_survives_failed_recheck(self) -> None:
        # 已知“有新版本”的事实不因后续检查失败而失效。
        ok = _ManifestServer(_manifest("9.9.9"))
        self.addCleanup(ok.close)
        _, first = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=ok.url,
            force=True,
        )
        self.assertEqual(first["status"], "available")
        self.assertFalse(first["lastCheckFailed"])
        broken = _ManifestServer(b"nope", status=503)
        self.addCleanup(broken.close)
        _, second = update_check.perform_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=broken.url,
            force=True,
        )
        self.assertEqual(second["status"], "available")
        self.assertEqual(second["latestVersion"], "9.9.9")
        self.assertIn("/releases/tag/v9.9.9", second["releaseUrl"])

    def test_view_recovers_after_later_success(self) -> None:
        ok = _ManifestServer(_manifest("2.2.3"))
        self.addCleanup(ok.close)
        broken = _ManifestServer(b"broken", status=500)
        self.addCleanup(broken.close)
        update_check.perform_check(
            data_dir=self.data_dir, current_version="2.2.3",
            url=broken.url, force=True,
        )
        _, recovered = update_check.perform_check(
            data_dir=self.data_dir, current_version="2.2.3",
            url=ok.url, force=True,
        )
        self.assertEqual(recovered["status"], "current")
        self.assertFalse(recovered["lastCheckFailed"])

    def test_view_directly_on_stale_sidecar_state(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        update_check.sidecar_path(self.data_dir).write_text(
            json.dumps(
                {
                    "schema": 1,
                    "lastAttemptAtUtc": "2026-08-18T10:00:00Z",
                    "lastSuccessAtUtc": "2026-08-17T10:00:00Z",
                    "lastErrorAtUtc": "2026-08-18T10:00:00Z",
                    "latestVersion": "2.2.3",
                    "latestTag": "v2.2.3",
                }
            ),
            encoding="utf-8",
        )
        summary = update_check.view(
            enabled=True, data_dir=self.data_dir, current_version="2.2.3"
        )
        self.assertEqual(summary["status"], "unknown")
        self.assertTrue(summary["lastCheckFailed"])

    def test_maybe_periodic_check_respects_interval(self) -> None:
        server = _ManifestServer(_manifest("9.9.9"))
        self.addCleanup(server.close)
        performed, _ = update_check.maybe_periodic_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=server.url,
        )
        self.assertTrue(performed)
        skipped, _ = update_check.maybe_periodic_check(
            data_dir=self.data_dir,
            current_version="2.2.3",
            url=server.url,
        )
        self.assertFalse(skipped)


class UpdateCheckApiTests(BackendTestCase):
    def test_system_status_reports_disabled_update_check_by_default(self) -> None:
        self.setup_system()
        self.login()
        payload = self.client.get("/api/v1/admin/system").get_json()
        self.assertEqual(payload["updateCheck"], {"enabled": False})
        blocked = self.write("POST", "/api/v1/admin/system/update-check")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(
            blocked.get_json()["error"]["code"], "UPDATE_CHECK_DISABLED"
        )

    def _enable_update_check(self, url: str) -> None:
        self.app.config["UPDATE_CHECK_ENABLED"] = True
        self.app.config["UPDATE_CHECK_URL"] = url

    def test_admin_manual_check_and_employee_boundary(self) -> None:
        server = _ManifestServer(_manifest("9.9.9"))
        self.addCleanup(server.close)
        self.setup_system()
        self.login()
        self._enable_update_check(server.url)
        payload = self.client.get("/api/v1/admin/system").get_json()
        self.assertTrue(payload["updateCheck"]["enabled"])
        self.assertEqual(payload["updateCheck"]["status"], "unknown")

        result = self.write("POST", "/api/v1/admin/system/update-check")
        self.assertEqual(result.status_code, 200, result.get_json())
        body = result.get_json()
        self.assertEqual(body["status"], "available")
        self.assertEqual(body["latestVersion"], "9.9.9")
        self.assertIn("/releases/tag/v9.9.9", body["releaseUrl"])

        throttled = self.write("POST", "/api/v1/admin/system/update-check")
        self.assertEqual(throttled.status_code, 429)
        self.assertEqual(
            throttled.get_json()["error"]["code"], "UPDATE_CHECK_THROTTLED"
        )

        refreshed = self.client.get("/api/v1/admin/system").get_json()
        self.assertEqual(refreshed["updateCheck"]["status"], "available")

        self.add_employee()
        employee_client = self.app.test_client()
        self.login("employee", "employee-pass-123", client=employee_client)
        forbidden = self.write(
            "POST",
            "/api/v1/admin/system/update-check",
            client=employee_client,
        )
        self.assertEqual(forbidden.status_code, 403)
        status = employee_client.get("/api/v1/admin/system")
        self.assertEqual(status.status_code, 403)

    def test_unauthenticated_requests_are_rejected(self) -> None:
        self.setup_system()
        response = self.client.post(
            "/api/v1/admin/system/update-check",
            headers={"Host": "localhost:8080"},
            json={},
        )
        # 与现有 admin 边界一致：未登录与员工同样稳定 403，不泄露更多信息。
        self.assertEqual(response.status_code, 403)


@unittest.skipIf(os.name == "nt", "macOS 自托管适配仅覆盖 POSIX")
class MacOsServiceAdaptationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_macos_runtime_executables_are_allowed(self) -> None:
        runtime = self.root / "runtime" / "bin"
        runtime.mkdir(parents=True)
        (runtime / "python3.13").write_text("", encoding="utf-8")
        with mock.patch.object(service_entrypoint, "PROGRAM_DIR", self.root):
            allowed = service_entrypoint._allowed_service_executables()
        self.assertIn(
            service_entrypoint._normalized_path(runtime / "python3.13"), allowed
        )

    def test_ensure_macos_install_identity_creates_pair_idempotently(self) -> None:
        data_dir = self.root / "data"
        app_dir = self.root / "app"
        app_dir.mkdir(parents=True)
        (app_dir / "EDITION").write_text(
            "macos-selfhost\n", encoding="utf-8"
        )
        with mock.patch.object(service_entrypoint, "APP_DIR", app_dir), \
                mock.patch.object(
                    service_entrypoint, "EDITION_MARKER_PATH", app_dir / "EDITION"
                ), \
                mock.patch.object(service_entrypoint, "DATA_DIR", data_dir), \
                mock.patch.object(
                    service_entrypoint, "INSTALL_INFO_PATH", data_dir / "install.json"
                ), \
                mock.patch.object(
                    service_entrypoint, "INSTALL_ID_PATH", data_dir / "install_id"
                ):
            service_entrypoint.ensure_macos_install_identity()
            record = service_entrypoint.load_install_identity()
            self.assertEqual(set(record), service_entrypoint.INSTALL_FIELDS)
            self.assertFalse(record["setup_complete"])
            # 幂等：第二次调用不改变任何文件。
            first = (data_dir / "install.json").read_bytes()
            service_entrypoint.ensure_macos_install_identity()
            self.assertEqual((data_dir / "install.json").read_bytes(), first)

    def test_ensure_macos_install_identity_ignores_other_editions(self) -> None:
        app_dir = self.root / "app"
        app_dir.mkdir(parents=True)
        data_dir = self.root / "data"
        with mock.patch.object(service_entrypoint, "APP_DIR", app_dir), \
                mock.patch.object(
                    service_entrypoint, "EDITION_MARKER_PATH", app_dir / "EDITION"
                ), \
                mock.patch.object(service_entrypoint, "DATA_DIR", data_dir), \
                mock.patch.object(
                    service_entrypoint, "INSTALL_INFO_PATH", data_dir / "install.json"
                ), \
                mock.patch.object(
                    service_entrypoint, "INSTALL_ID_PATH", data_dir / "install_id"
                ):
            service_entrypoint.ensure_macos_install_identity()
            self.assertFalse(data_dir.exists())

    def test_half_identity_state_fails_loudly(self) -> None:
        app_dir = self.root / "app"
        data_dir = self.root / "data"
        app_dir.mkdir(parents=True)
        data_dir.mkdir()
        (app_dir / "EDITION").write_text(
            "macos-selfhost\n", encoding="utf-8"
        )
        (data_dir / "install_id").write_text(
            "01234567-89ab-4cde-8fab-cdef01234567\n", encoding="ascii"
        )
        with mock.patch.object(
            service_entrypoint, "EDITION_MARKER_PATH", app_dir / "EDITION"
        ), \
                mock.patch.object(service_entrypoint, "DATA_DIR", data_dir), \
                mock.patch.object(
                    service_entrypoint, "INSTALL_INFO_PATH", data_dir / "install.json"
                ), \
                mock.patch.object(
                    service_entrypoint, "INSTALL_ID_PATH", data_dir / "install_id"
                ):
            with self.assertRaises(RuntimeError):
                service_entrypoint.ensure_macos_install_identity()


if __name__ == "__main__":
    unittest.main()
