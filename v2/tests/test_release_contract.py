from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (V2_ROOT / relative).read_text(encoding="utf-8")


class CrossLayerReleaseContractTests(unittest.TestCase):
    def test_product_and_frontend_versions_are_v2(self) -> None:
        self.assertEqual(read("VERSION").strip(), "2.0.0")
        package = json.loads(read("frontend/package.json"))
        self.assertEqual(package["version"], "2.0.0")
        self.assertEqual(package["name"], "meeting-room-v2-frontend")
        self.assertEqual(package["scripts"]["build"], "vite build")

    def test_api_schema_and_role_enum_are_shared(self) -> None:
        frontend = "\n".join(
            read(relative)
            for relative in (
                "frontend/src/api.js",
                "frontend/src/domain.js",
                "frontend/src/App.jsx",
            )
        )
        backend = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((V2_ROOT / "backend").rglob("*.py"))
        )
        self.assertIn('API_BASE = "/api/v1"', frontend)
        self.assertIn("('admin', 'employee')", backend)
        self.assertNotRegex(frontend + backend, r"(?<![A-Za-z])staff(?![A-Za-z])")

    def test_production_frontend_has_no_synthetic_business_state(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((V2_ROOT / "frontend" / "src").glob("*"))
            if path.is_file()
        )
        for forbidden in (
            "demo123",
            "demo1234",
            "TEST-2026",
            "INITIAL_CALENDAR_BOOKINGS",
            "Date.now()",
            "/api/v2",
        ):
            self.assertNotIn(forbidden, source)

    def test_database_filename_and_generation_match_updater(self) -> None:
        app_factory = read("backend/v2app/__init__.py")
        database = read("backend/v2app/db.py")
        updater = read("installer/update_core.py")
        self.assertIn('data_dir / "reservation.db"', app_factory)
        self.assertIn('"data" / "reservation.db"', updater)
        self.assertRegex(database, r"PRODUCT_GENERATION\s*=\s*2")
        self.assertRegex(database, r"SCHEMA_VERSION\s*=\s*1")

    def test_installer_and_backend_share_secret_and_health_contracts(self) -> None:
        installer = read("installer/installer_core.py")
        identity = read("backend/v2app/runtime/identity.py")
        app_factory = read("backend/v2app/__init__.py")
        self.assertIn("secrets.token_hex(32)", installer)
        self.assertIn('r"[0-9a-f]{64}"', identity)
        for key in (
            "product_generation",
            "install_id",
            "setup_complete",
            "bind_mode",
            "port",
        ):
            self.assertIn(f'"{key}"', installer)
            self.assertIn(f'"{key}"', app_factory)

    def test_frontend_build_output_matches_backend_static_default(self) -> None:
        vite = read("frontend/vite.config.mjs")
        app_factory = read("backend/v2app/__init__.py")
        self.assertIn('outDir: "dist/client"', vite)
        self.assertIn('/ "dist" / "client"', app_factory)

    def test_service_runtime_sources_and_payload_dependencies_are_present(self) -> None:
        for relative in (
            "backend/service.py",
            "backend/server.py",
            "backend/v2app/runtime/__init__.py",
            "backend/v2app/runtime/identity.py",
            "backend/v2app/runtime/install_state.py",
        ):
            self.assertTrue((V2_ROOT / relative).is_file(), relative)
        assembler = read("installer/assemble_payload.py")
        self.assertIn(
            '("service.py", "server.py", "backup.py", "requirements.txt")',
            assembler,
        )
        self.assertIn('"STATIC_DIR": str(SERVICE_DIR / "static")', read("backend/service.py"))

    def test_runtime_dependencies_are_exact(self) -> None:
        requirements = [
            line.strip()
            for line in read("backend/requirements.txt").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(requirements, ["Flask==3.1.3", "waitress==3.0.2"])
        self.assertTrue(all("==" in requirement for requirement in requirements))

    def test_public_frontend_contract_rejects_extra_fields(self) -> None:
        contract = read("frontend/src/public-contract.js")
        self.assertIn(
            'new Set(["serverDate", "serverTime", "status", "lastUpdatedAt", "rooms"])',
            contract,
        )
        self.assertIn('new Set(["maskedPartyName", "start", "end"])', contract)
        for private_field in ("caseNumber", "notes", "ownerId", "department", "tagId"):
            self.assertNotIn(f'"{private_field}"', contract)

    def test_installer_is_fresh_install_only(self) -> None:
        builder = read("installer/build_package.py")
        core = read("installer/installer_core.py")
        self.assertIn('"kind": "fresh-install"', builder)
        self.assertIn('SERVICE_ENTRYPOINT = "_程序文件/service.py"', core)
        self.assertNotIn("os.walk(Path.home()", core)
        self.assertNotIn("rglob(\"reservation.db\")", core)


if __name__ == "__main__":
    unittest.main()
