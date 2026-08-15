from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from v2.installer.frontend_supply_chain import build_frontend_component_evidence


V2_ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (V2_ROOT / relative).read_text(encoding="utf-8")


class CrossLayerReleaseContractTests(unittest.TestCase):
    def test_product_and_frontend_versions_are_v2(self) -> None:
        self.assertEqual(read("VERSION").strip(), "2.1.0")
        package = json.loads(read("frontend/package.json"))
        self.assertEqual(package["version"], "2.1.0")
        self.assertEqual(package["name"], "meeting-room-v2-frontend")
        self.assertEqual(package["scripts"]["build"], "vite build")
        self.assertIn('PRODUCT_VERSION = "V2.1.0"', read("backend/v2app/__init__.py"))
        installer = read("installer/installer_core.py")
        self.assertIn('VERSION = "2.1.0"', installer)
        self.assertIn('RELEASE = "V2.1.0"', installer)

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
        self.assertIn('getActivity: () => request("/activity")', frontend)
        self.assertNotIn("getActivityDay", frontend)
        self.assertIn('url_prefix="/api/v1/activity"', backend)

    def test_api_contract_covers_upcoming_room_impact_and_read_only_integrations(self) -> None:
        contract = read("docs/API-CONTRACT.md")
        reservations = read("backend/v2app/api/reservations.py")
        reservation_service = read("backend/v2app/services/reservations.py")
        admin = read("backend/v2app/api/admin.py")
        system = read("backend/v2app/api/system.py")

        self.assertIn('@bp.get("/upcoming")', reservations)
        self.assertIn("GET /api/v1/reservations/upcoming", contract)
        self.assertIn("当前登录用户本人", contract)
        self.assertIn("r.owner_user_id = ? AND r.status = 'active'", reservation_service)

        self.assertIn('@bp.get("/rooms/<room_id>/deletion-impact")', admin)
        self.assertIn("GET /api/v1/rooms/{id}/deletion-impact", contract)
        self.assertIn("LIMIT 50", admin)
        for field in ('"room"', '"total"', '"items"'):
            self.assertIn(field, admin)
        self.assertIn("前 50 个完整预约投影", contract)
        self.assertIn("ROOM_HAS_FUTURE_BOOKINGS", contract)
        self.assertIn("total` 可能大于 conflicts 长度", contract)

        self.assertIn('TOKEN_SCOPES = {"rooms:read", "availability:read", "health:read"}', system)
        for endpoint in ("rooms", "availability", "health"):
            self.assertIn(f'@bp.get("/integration/{endpoint}")', system)
            self.assertIn(f"GET /api/v1/integration/{endpoint}", contract)
        for code in ("TOKEN_REQUIRED", "TOKEN_INVALID", "TOKEN_EXPIRED", "TOKEN_SCOPE_FORBIDDEN"):
            self.assertIn(code, system)
            self.assertIn(code, contract)
        self.assertIn('"slots": slots', system)
        self.assertIn('"productVersion": current_app.config["PRODUCT_VERSION"]', system)

    def test_personal_activity_is_server_aggregated_and_current_user_scoped(self) -> None:
        activity = read("backend/v2app/api/activity.py")
        frontend = read("frontend/src/features/profile/PersonalCenter.jsx")
        self.assertIn('actor = current_user()', activity)
        self.assertIn("owner_user_id = ? AND status = 'active'", activity)
        self.assertIn("end_time <= ?", activity)
        self.assertIn("currentMonthCompleted", activity)
        self.assertNotIn('/days/', activity)
        self.assertNotIn('daily_rows', activity)
        self.assertNotIn("ownerId", activity)
        self.assertIn("活动数据", frontend)
        self.assertNotIn("最近完成", frontend)
        self.assertNotIn("profile-heatmap", frontend)

    def test_external_reminder_template_default_and_privacy_match_across_layers(self) -> None:
        default_template = (
            "【笔录提醒】{当事人姓名}您好，您预约的笔录时间为{日期} {开始时间}，"
            "地点：{笔录室}，请提前到达。如有变动我们会再联系您。"
        )
        database = read("backend/v2app/db.py")
        frontend = read("frontend/src/features/reminders/reminder-template.js")
        contract = read("docs/API-CONTRACT.md")
        database_module = ast.parse(database)
        backend_default = next(
            ast.literal_eval(node.value)
            for node in database_module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "DEFAULT_REMINDER_TEMPLATE"
                for target in node.targets
            )
        )
        frontend_default = json.loads(
            re.search(
                r"export const DEFAULT_REMINDER_TEMPLATE = (\".*\");",
                frontend,
            ).group(1)
        )
        self.assertEqual(backend_default, default_template)
        self.assertEqual(frontend_default, default_template)
        for token in ("{当事人姓名}", "{日期}", "{开始时间}", "{结束时间}", "{笔录室}"):
            self.assertIn(token, frontend)
            self.assertIn(token, contract)
        variable_block = frontend.split("REMINDER_TEMPLATE_VARIABLES = [", 1)[1].split("];", 1)[0]
        for forbidden in ("案号", "用途", "备注", "caseNumber", "purpose", "notes"):
            self.assertNotIn(forbidden, variable_block)

    def test_production_frontend_has_no_synthetic_business_state(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((V2_ROOT / "frontend" / "src").rglob("*"))
            if path.is_file() and path.suffix in {".css", ".html", ".js", ".jsx", ".json", ".mjs"}
        )
        for forbidden in (
            "demo123",
            "demo1234",
            "TEST-2026",
            "INITIAL_CALENDAR_BOOKINGS",
            "/api/v2",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotRegex(
            source,
            r"\b(?:id|reservationId)\s*[:=]\s*(?:String\()?Date\.now\(\)",
        )

    def test_database_filename_and_generation_match_updater(self) -> None:
        app_factory = read("backend/v2app/__init__.py")
        database = read("backend/v2app/db.py")
        updater = read("installer/update_core.py")
        self.assertIn('data_dir / "reservation.db"', app_factory)
        self.assertIn('"data" / "reservation.db"', updater)
        self.assertRegex(database, r"PRODUCT_GENERATION\s*=\s*2")
        self.assertRegex(database, r"SCHEMA_VERSION\s*=\s*2")
        self.assertIn("SUPPORTED_SCHEMA_VERSIONS = frozenset({1, SCHEMA_VERSION})", database)
        self.assertIn("SUPPORTED_DATABASE_SCHEMA_VERSIONS", updater)

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
        service = read("backend/service.py")
        self.assertIn('outDir: "dist/client"', vite)
        self.assertIn('/ "dist" / "client"', app_factory)
        self.assertIn('"STATIC_DIR": str(APP_DIR / "static")', service)

    def test_service_runtime_sources_and_payload_dependencies_are_present(self) -> None:
        for relative in (
            "backend/service.py",
            "backend/server.py",
            "backend/backup.py",
            "backend/restore.py",
            "backend/requirements-win-amd64.lock",
            "backend/v2app/runtime/__init__.py",
            "backend/v2app/runtime/identity.py",
            "backend/v2app/runtime/install_state.py",
        ):
            self.assertTrue((V2_ROOT / relative).is_file(), relative)
        assembler = read("installer/assemble_payload.py")
        for name in (
            "service.py",
            "server.py",
            "backup.py",
            "restore.py",
            "requirements.txt",
            "requirements-win-amd64.lock",
        ):
            self.assertIn(f'"{name}"', assembler)
        self.assertIn('app / "static"', assembler)

    def test_runtime_dependencies_are_exact(self) -> None:
        requirements = [
            line.strip()
            for line in read("backend/requirements.txt").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(requirements, ["Flask==3.1.3", "waitress==3.0.2"])
        self.assertTrue(all("==" in requirement for requirement in requirements))

        lock = read("backend/requirements-win-amd64.lock")
        locked = re.findall(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\\\s]+) \\\n"
            r"\s+--hash=sha256:([0-9a-f]{64})$",
            lock,
            flags=re.MULTILINE,
        )
        self.assertEqual(len(locked), 11)
        self.assertEqual(len({name.casefold() for name, _, _ in locked}), 11)
        self.assertIn(("Flask", "3.1.3"), {(name, version) for name, version, _ in locked})
        self.assertIn(("waitress", "3.0.2"), {(name, version) for name, version, _ in locked})

    def test_bootstrap_can_rebuild_a_broken_or_moved_virtual_environment(self) -> None:
        bootstrap = read("scripts/bootstrap-dev.sh")
        self.assertIn('if [ -x "$backend_root/.venv/bin/python" ]', bootstrap)
        self.assertIn(
            'uv venv --clear --cache-dir "$uv_cache" --python "$python_bin"',
            bootstrap,
        )

    def test_frozen_runtime_layout_is_isolated_and_traceable(self) -> None:
        core = read("installer/installer_core.py")
        builder = read("installer/build_runtime.py")
        package_builder = read("installer/build_package.py")
        self.assertIn(
            'RUNTIME_PTH_LINES = ("python313.zip", ".", "Lib\\\\site-packages", "..\\\\app")',
            core,
        )
        module = ast.parse(core)
        pth_lines = next(
            ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "RUNTIME_PTH_LINES"
                for target in node.targets
            )
        )
        self.assertNotIn("..", pth_lines)
        self.assertNotIn("import site", pth_lines)
        self.assertIn("APPROVED_PYTHON_SOURCE_URL", builder)
        self.assertIn("python-3.13.14-embed-amd64.zip", core)
        self.assertIn(
            "90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907",
            core,
        )
        self.assertIn(
            "e1eca4d7649cd615d9d31da8ef961cdc157be0458a289c3bb27bdd619f42a1cc",
            core,
        )
        self.assertIn("runtime 完整文件树不是项目批准的正式候选", core)
        for material, constant in (
            ("runtime-provenance.json", "RUNTIME_PROVENANCE_FILE"),
            ("requirements.lock", "RUNTIME_LOCK_FILE"),
            ("sbom.cdx.json", "RUNTIME_SBOM_FILE"),
            ("THIRD-PARTY-NOTICES.txt", "RUNTIME_NOTICES_FILE"),
        ):
            self.assertIn(material, core)
            self.assertIn(constant, package_builder)

    def test_artifact_sbom_binds_frontend_production_lock(self) -> None:
        evidence = json.loads(
            build_frontend_component_evidence(
                V2_ROOT / "frontend" / "package-lock.json"
            )
        )
        names = {component["name"] for component in evidence["components"]}
        self.assertTrue({"react", "react-dom", "@phosphor-icons/react"}.issubset(names))
        self.assertNotIn("vite", names)
        assembler = read("installer/assemble_payload.py")
        package_builder = read("installer/build_package.py")
        reproducible = read("../.github/scripts/v2-reproducible-build.sh")
        self.assertIn('parser.add_argument("--frontend-lock"', assembler)
        self.assertIn("make_artifact_sbom", package_builder)
        self.assertIn("--frontend-lock v2/frontend/package-lock.json", reproducible)

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
        self.assertIn('SERVICE_ENTRYPOINT = "_程序文件/app/service.py"', core)
        self.assertIn('PRODUCT_DIRECTORY_NAME = "会议室预约系统V2"', core)
        self.assertNotIn("os.walk(Path.home()", core)
        self.assertNotIn("rglob(\"reservation.db\")", core)

    def test_windows_candidate_gate_distinguishes_infrastructure_failures(self) -> None:
        launcher = read("installer/安装V2.1.0.bat")
        workflow = (V2_ROOT.parent / ".github/workflows/release-candidate.yml").read_text(
            encoding="utf-8"
        )
        gate = (V2_ROOT.parent / ".github/scripts/v2-windows-candidate-gate.ps1").read_text(
            encoding="utf-8"
        )
        for marker, code in (
            ("MISSING_TOOL_DIR", 11),
            ("MISSING_RUNTIME_PYTHON", 12),
            ("MISSING_PRODUCT_INPUT", 13),
            ("PYTHON_START_FAILED", 14),
        ):
            self.assertIn(f"MRV2_GATE={marker}", launcher)
            self.assertIn(f"exit /b {code}", launcher)
        for marker in (
            "MISSING_LAUNCHER",
            "MISSING_TOOL_DIR",
            "MISSING_RUNTIME_PYTHON",
            "MISSING_PRODUCT_INPUT",
            "PYTHON_START_FAILED",
            "PRODUCT_RC_1",
            "PRODUCT_RC_0",
        ):
            self.assertIn(f"MRV2_GATE={marker}", gate)
        self.assertIn("MRV2_INSTALLER_RESULT=%INSTALL_RC%", launcher)
        self.assertIn('"missing-launcher"', gate)
        self.assertIn('"missing-python"', gate)
        self.assertIn('"missing-product"', gate)
        self.assertIn('"④ 停止本次后台系统.bat"', gate)
        self.assertIn("Invoke-CandidateBat $installRoot", gate)
        self.assertIn("formal candidate cleanup", gate)
        self.assertIn(
            "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
            workflow,
        )
        self.assertIn("v2-windows-candidate-gate.ps1", workflow)
        reproducible = (V2_ROOT.parent / ".github/scripts/v2-reproducible-build.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("MRV2_REPRODUCIBLE_BUILD=PASS", reproducible)
        self.assertIn("for label in first second", reproducible)
        self.assertIn("npm ci", reproducible)
        self.assertIn("v2.installer.build_runtime", reproducible)
        self.assertIn("v2.installer.assemble_payload", reproducible)
        self.assertIn("v2.installer.build_package", reproducible)
        self.assertIn("v2-reproducible-build.sh", workflow)
        self.assertIn('version=$(tr -d', reproducible)
        self.assertIn('artifact="会议室预约系统-V${version}-安装包.zip"', reproducible)
        self.assertIn('$version = (Get-Content -LiteralPath "v2/VERSION"', gate)
        self.assertIn('$artifactName = "会议室预约系统-V$version-安装包.zip"', gate)
        self.assertIn('$launcherName = "安装V$version.bat"', gate)
        self.assertNotIn("V2.0.0-安装包.zip", workflow)
        self.assertNotIn("V2.0.0-安装包.zip", reproducible)
        self.assertNotIn("V2.0.0-安装包.zip", gate)

    def test_admin_edit_uses_owner_personal_tags_across_layers(self) -> None:
        bootstrap = read("backend/v2app/api/core.py")
        domain = read("frontend/src/domain.js")
        app = read("frontend/src/App.jsx")
        self.assertIn('item["personalTags"] = _serialize_personal_tags(row)', bootstrap)
        self.assertIn("users.find((user) => user?.id === booking.ownerId)", domain)
        self.assertIn('reason: "OWNER_TAGS_MISSING"', domain)
        self.assertIn("ownerTags.ownerTagsAvailable", app)

    def test_v210_update_core_is_explicitly_non_production(self) -> None:
        updater = read("installer/update_core.py")
        installer_readme = read("installer/README.md")
        self.assertIn("PRODUCTION_UPDATE_SUPPORTED = False", updater)
        self.assertIn("V2.1.0 非生产能力", installer_readme)
        self.assertIn("不代表 V2.1.0 支持在线升级", installer_readme)

    def test_operator_failure_messages_are_actionable_and_do_not_echo_exceptions(self) -> None:
        service = read("backend/service.py")
        backup = read("backend/backup.py")
        restore = read("backend/restore.py")
        self.assertIn("8080 端口已被其他程序占用", service)
        self.assertIn("_程序文件\\\\logs\\\\backup.log", backup)
        self.assertIn("_程序文件\\\\logs\\\\restore.log", restore)
        self.assertNotIn('print(f"V2 服务操作失败：{error}"', service)
        self.assertNotIn('print(f"备份失败：{error}"', backup)
        self.assertNotIn('print(f"恢复失败：{error}"', restore)


if __name__ == "__main__":
    unittest.main()
