from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any


TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import 制作正式更新包 as builder  # noqa: E402


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _payload_files(engine: ModuleType, version: str, marker: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for relative in engine.TOP_LEVEL_FILES:
        files[relative] = f"@echo off\r\nrem {marker}\r\n".encode("utf-8")
    for relative in engine.PROGRAM_FILES:
        if relative == "_程序文件/版本.txt":
            files[relative] = f"{version}\n".encode("utf-8")
        elif relative == "_程序文件/requirements.txt":
            files[relative] = b"Flask==3.1.1\nwaitress==3.0.2\n"
        else:
            files[relative] = f"# {marker}: {relative}\n".encode("utf-8")
    files["_程序文件/static/app.css"] = f"/* {marker} */\n".encode("utf-8")
    files["_程序文件/templates/index.html"] = (
        f"<!doctype html><title>{marker}</title>\n"
    ).encode("utf-8")
    return files


def _payload(engine: ModuleType, version: str, marker: str) -> Any:
    files = _payload_files(engine, version, marker)
    records = tuple(engine._managed_records_from_files(files))
    return engine.Payload(
        version=version,
        zip_name=f"{marker}.zip",
        zip_sha256=hashlib.sha256(marker.encode("utf-8")).hexdigest(),
        files=files,
        records=records,
    )


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


class FormalUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        engine_path = self.root / "_formal_engine.py"
        engine_path.write_bytes(builder._formal_engine_bytes())
        self.engine = _load_module(
            engine_path,
            f"_formal_engine_test_{id(self)}",
        )
        self.tool_root = self.root / "候选更新工具 & (1)"
        runtime = self.tool_root / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "python.exe").write_bytes(b"frozen-python\n")
        (runtime / "pythonw.exe").write_bytes(b"frozen-pythonw\n")
        (runtime / "python311.dll").write_bytes(b"frozen-dll\n")
        self.baseline = _payload(self.engine, "1.0.2", "baseline-v102")
        self.target = _payload(self.engine, "1.0.3", "target-v103")
        runtime_records = tuple(self.engine._records_for_tree(runtime))
        self.bundle = self.engine.Bundle(
            tool_root=self.tool_root,
            release=self.engine.REPAIR_RELEASE,
            baseline=self.baseline,
            target=self.target,
            runtime_records=runtime_records,
            runtime_tree_sha256=self.engine._tree_digest(runtime_records),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _make_install(self, version: str, marker: str) -> Path:
        install = self.root / f"客户安装-{version}-{marker}"
        install.mkdir()
        _write_files(install, _payload_files(self.engine, version, marker))
        if version == "1.0.0":
            (install / "_程序文件" / "版本.txt").unlink()
        data = install / "_程序文件" / "data"
        data.mkdir()
        database = data / "reservation.db"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                f"""
                CREATE TABLE app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                {
                    ""
                    if version == "1.0.0"
                    else "INSERT INTO app_meta(key, value) "
                    "VALUES ('schema_version', '1');"
                }
                CREATE TABLE reservations (
                    id INTEGER PRIMARY KEY,
                    subject TEXT NOT NULL
                );
                INSERT INTO reservations(id, subject)
                    VALUES (7, '客户预约 & (1)');
                """
            )
            connection.commit()
        finally:
            connection.close()
        (data / ".secret_key").write_bytes(b"\x00customer-secret\xff")
        (data / "install_id").write_text("fixed-install-id\n", encoding="utf-8")
        (data / "局域网访问地址状态.json").write_text(
            '{"url":"http://192.168.1.8:8080"}\n',
            encoding="utf-8",
        )
        unknown = data / "客户附件" / "记录.bin"
        unknown.parent.mkdir()
        unknown.write_bytes(bytes(range(64)))
        (install / "_程序文件" / "backups").mkdir()
        (install / "_程序文件" / "logs").mkdir()
        return install

    def _run(self, install: Path, *, validate_target=None, fault_hook=None) -> None:
        updater = self.engine.RepairUpdater(
            self.bundle,
            install,
            self.engine.PassiveSystemController(),
            validate_target=validate_target
            or (lambda _root, _data, _snapshot, _log: None),
            fault_hook=fault_hook,
        )
        updater.run()

    def _assert_target(self, install: Path) -> None:
        self.engine._assert_installed_payload(
            install,
            self.target,
            include_version=True,
        )

    def test_engine_identity_and_database_contract_are_v103_only(self) -> None:
        self.assertEqual(self.engine.BASELINE_VERSION, "1.0.2")
        self.assertEqual(self.engine.TARGET_VERSION, "1.0.3")
        self.assertEqual(self.engine.REPAIR_RELEASE, builder.RELEASE)
        self.assertEqual(self.engine.STATE_NAME, "_正式更新状态.json")
        source = (TOOL_DIR.parent / "源代码工作区" / "app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("SCHEMA_VERSION = 1", source)
        self.assertIn(
            "MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = []",
            source,
        )

    def test_v100_v101_and_clean_v102_reach_v103_without_data_changes(self) -> None:
        for version in ("1.0.0", "1.0.1", "1.0.2"):
            with self.subTest(version=version):
                install = self._make_install(version, f"from-{version}")
                data = install / "_程序文件" / "data"
                before = _tree_bytes(data)
                self._run(install)
                self._assert_target(install)
                self.assertEqual(_tree_bytes(data), before)

    def test_v100_precheck_allows_only_a_missing_schema_marker(self) -> None:
        install = self._make_install("1.0.0", "schema-marker")
        database = install / "_程序文件" / "data" / "reservation.db"

        with self.assertRaisesRegex(
            self.engine.UpdateError, "数据库结构版本不是 1"
        ):
            self.engine._database_integrity_check(database)
        self.engine._database_integrity_check(
            database, allow_missing_schema_version=True
        )

        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "INSERT INTO app_meta(key, value) VALUES ('schema_version', 'broken')"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(
            self.engine.UpdateError, "数据库结构版本不是 1"
        ):
            self.engine._database_integrity_check(
                database, allow_missing_schema_version=True
            )

    def test_repeat_run_is_idempotent_and_preserves_customer_state(self) -> None:
        install = self._make_install("1.0.2", "repeat")
        data = install / "_程序文件" / "data"
        before = _tree_bytes(data)
        self._run(install)
        first_program = {
            path: content
            for path, content in _tree_bytes(install).items()
            if path in self.target.files
        }
        self._run(install)
        self._assert_target(install)
        self.assertEqual(_tree_bytes(data), before)
        self.assertEqual(
            {
                path: content
                for path, content in _tree_bytes(install).items()
                if path in self.target.files
            },
            first_program,
        )

    def test_target_failure_rolls_back_to_verified_v102_and_preserves_data(self) -> None:
        install = self._make_install("1.0.0", "rollback")
        data = install / "_程序文件" / "data"
        before = _tree_bytes(data)

        def reject_target(_root: Path, _data: Path, _snapshot: Path, _log: Any) -> None:
            raise RuntimeError("simulated target validation failure")

        with self.assertRaisesRegex(RuntimeError, "target validation failure"):
            self._run(install, validate_target=reject_target)
        self.engine._assert_installed_payload(
            install,
            self.baseline,
            include_version=True,
        )
        self.assertEqual(_tree_bytes(data), before)
        state = json.loads(
            (
                install
                / "_程序文件"
                / self.engine.STATE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(state["stage"], "baseline_rollback_complete")

    def test_old_cumulative_residue_is_normalized_and_evidence_is_preserved(self) -> None:
        install = self._make_install("1.0.1", "legacy")
        program = install / "_程序文件"
        data_before = _tree_bytes(program / "data")
        (program / self.engine.LEGACY_STATE_NAME).write_text(
            json.dumps({"Stage": "program_replaced"}),
            encoding="utf-8",
        )
        (program / self.engine.LEGACY_LOCK_NAME).write_bytes(b"old-lock")
        evidence = (
            program
            / self.engine.LEGACY_ROLLBACK_NAME
            / "old"
            / "data"
            / "reservation.db"
        )
        evidence.parent.mkdir(parents=True)
        evidence.write_bytes(b"old-evidence")
        self._run(install)
        self._assert_target(install)
        self.assertEqual(_tree_bytes(program / "data"), data_before)
        self.assertEqual(evidence.read_bytes(), b"old-evidence")
        self.assertFalse((program / self.engine.LEGACY_STATE_NAME).exists())
        self.assertFalse((program / self.engine.LEGACY_LOCK_NAME).exists())

    def test_r1_incomplete_state_is_finished_before_v103_transaction(self) -> None:
        recovery_path = self.root / "_v102_engine.py"
        recovery_path.write_bytes((TOOL_DIR / "覆盖更新.py").read_bytes())
        recovery = _load_module(
            recovery_path,
            f"_recovery_engine_test_{id(self)}",
        )
        recovery_baseline = _payload(recovery, "1.0.1", "recovery-v101")
        recovery_bundle = recovery.Bundle(
            tool_root=self.tool_root,
            release=recovery.REPAIR_RELEASE,
            baseline=recovery_baseline,
            target=self.baseline,
            runtime_records=self.bundle.runtime_records,
            runtime_tree_sha256=self.bundle.runtime_tree_sha256,
        )
        install = self._make_install("1.0.1", "r1-state")
        data = install / "_程序文件" / "data"
        before = _tree_bytes(data)

        def interrupt(stage: str) -> None:
            if stage == "target_verified":
                raise RuntimeError("simulated r1 interruption")

        with self.assertRaisesRegex(RuntimeError, "r1 interruption"):
            recovery.RepairUpdater(
                recovery_bundle,
                install,
                recovery.PassiveSystemController(),
                validate_target=lambda _root, _data, _snapshot, _log: None,
                fault_hook=interrupt,
            ).run()
        self.assertTrue(
            (install / "_程序文件" / recovery.STATE_NAME).is_file()
        )

        recovery.RepairUpdater(
            recovery_bundle,
            install,
            recovery.PassiveSystemController(),
            validate_target=lambda _root, _data, _snapshot, _log: None,
        ).run()
        self.assertFalse(
            (install / "_程序文件" / recovery.STATE_NAME).exists()
        )
        self._run(install)
        self._assert_target(install)
        self.assertEqual(_tree_bytes(data), before)


if __name__ == "__main__":
    unittest.main()
