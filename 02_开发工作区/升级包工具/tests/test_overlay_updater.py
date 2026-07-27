from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import 覆盖更新 as updater  # noqa: E402


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _payload_files(version: str, marker: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for relative in updater.TOP_LEVEL_FILES:
        files[relative] = (
            f"@echo off\r\nrem {marker}: {relative}\r\n"
        ).encode("utf-8")
    for relative in updater.PROGRAM_FILES:
        if relative == "_程序文件/版本.txt":
            files[relative] = (version + "\n").encode("utf-8")
        elif relative == "_程序文件/requirements.txt":
            files[relative] = b"Flask==3.1.1\nwaitress==3.0.2\n"
        else:
            files[relative] = (
                f"# {marker}: {relative}\nVALUE = {marker!r}\n"
            ).encode("utf-8")
    files["_程序文件/static/app.css"] = (
        f"/* {marker} */\nbody {{ color: #123456; }}\n"
    ).encode("utf-8")
    files["_程序文件/static/嵌套/状态.js"] = (
        f"window.release = {marker!r};\n"
    ).encode("utf-8")
    files["_程序文件/templates/index.html"] = (
        f"<!doctype html><title>{marker}</title>\n"
    ).encode("utf-8")
    return files


def _payload(version: str, marker: str, zip_name: str) -> updater.Payload:
    files = _payload_files(version, marker)
    records = tuple(updater._managed_records_from_files(files))
    return updater.Payload(
        version=version,
        zip_name=zip_name,
        zip_sha256=hashlib.sha256(
            (zip_name + "\0" + marker).encode("utf-8")
        ).hexdigest(),
        files=files,
        records=records,
    )


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        destination = root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


class OverlayUpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.tool_root = self.root / "修复工具 & 离线包 (1)"
        runtime = self.tool_root / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "python.exe").write_bytes(b"frozen-python\n")
        (runtime / "pythonw.exe").write_bytes(b"frozen-pythonw\n")
        (runtime / "python311.dll").write_bytes(b"frozen-runtime-dll\n")

        self.baseline = _payload("1.0.1", "baseline-v101", "baseline.zip")
        self.target = _payload("1.0.2", "target-v102", "target.zip")
        runtime_records = tuple(updater._records_for_tree(runtime))
        self.bundle = updater.Bundle(
            tool_root=self.tool_root,
            release=updater.REPAIR_RELEASE,
            baseline=self.baseline,
            target=self.target,
            runtime_records=runtime_records,
            runtime_tree_sha256=updater._tree_digest(runtime_records),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _make_install(
        self,
        *,
        relative: str = "客户安装",
    ) -> Path:
        install_root = self.root.joinpath(*relative.split("/"))
        install_root.mkdir(parents=True)
        _write_files(install_root, dict(self.baseline.files))

        program_root = install_root / "_程序文件"
        data_root = program_root / "data"
        data_root.mkdir()
        database = data_root / "reservation.db"
        connection = sqlite3.connect(str(database))
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL
                );
                CREATE TABLE reservations (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    subject TEXT NOT NULL
                );
                INSERT INTO app_meta(key, value)
                    VALUES ('schema_version', '1');
                INSERT INTO users(id, display_name)
                    VALUES (1, '人社数据团002');
                INSERT INTO reservations(id, user_id, subject)
                    VALUES (7, 1, '周一例会 & 项目复盘 (1)');
                """
            )
            connection.commit()
        finally:
            connection.close()
        (data_root / ".secret_key").write_bytes(b"\x00customer-secret\xff")
        (data_root / "install_id").write_text(
            "customer-install-id\n", encoding="utf-8"
        )
        (data_root / "lan_state.json").write_text(
            '{"host":"192.168.1.8","trusted":true}\n',
            encoding="utf-8",
        )
        attachment = data_root / "附件 & 导出 (1)" / "会议记录.bin"
        attachment.parent.mkdir()
        attachment.write_bytes(bytes(range(256)))

        backups = program_root / "backups"
        backups.mkdir()
        (backups / "客户原有备份.db").write_bytes(b"do-not-delete")
        (program_root / "logs").mkdir()
        return install_root

    def _run(
        self,
        install_root: Path,
        *,
        controller: updater.PassiveSystemController | None = None,
        validate_target=None,
        fault_hook=None,
    ) -> updater.PassiveSystemController:
        controller = controller or updater.PassiveSystemController(
            updater.TaskState(True, True, True, True)
        )
        repair = updater.RepairUpdater(
            self.bundle,
            install_root,
            controller,
            validate_target=validate_target
            or (lambda _root, _data, _snapshot, _log: None),
            fault_hook=fault_hook,
        )
        repair.run()
        return controller

    def _assert_target_installed(self, install_root: Path) -> None:
        updater._assert_installed_payload(
            install_root, self.target, include_version=True
        )
        self.assertEqual(
            (install_root / "_程序文件" / "版本.txt").read_text(
                encoding="utf-8"
            ).strip(),
            "1.0.2",
        )

    def _assert_baseline_installed(self, install_root: Path) -> None:
        updater._assert_installed_payload(
            install_root, self.baseline, include_version=True
        )
        self.assertEqual(
            (install_root / "_程序文件" / "版本.txt").read_text(
                encoding="utf-8"
            ).strip(),
            "1.0.1",
        )

    def _install_frozen_runtime(self, install_root: Path) -> Path:
        runtime = install_root / "_程序文件" / "runtime"
        if runtime.exists():
            if runtime.is_dir():
                shutil.rmtree(runtime)
            else:
                runtime.unlink()
        shutil.copytree(self.tool_root / "runtime", runtime)
        return runtime

    def test_clean_v101_upgrades_to_v102_without_changing_data(self) -> None:
        install_root = self._make_install()
        data_root = install_root / "_程序文件" / "data"
        data_before = _tree_bytes(data_root)

        controller = self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertEqual(_tree_bytes(data_root), data_before)
        self.assertEqual(controller.restored, controller.state)
        self.assertFalse(
            (install_root / "_程序文件" / updater.STATE_NAME).exists()
        )
        permanent_backups = list(
            (install_root / "_程序文件" / "backups").glob(
                "pre_v102_repair_*.db"
            )
        )
        self.assertEqual(len(permanent_backups), 1)
        updater._database_integrity_check(permanent_backups[0])

    def test_partial_legacy_v102_residue_is_normalized_and_preserved(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_root = program_root / "data"
        data_before = _tree_bytes(data_root)
        backup_sentinel = program_root / "backups" / "客户原有备份.db"
        log_sentinel = program_root / "logs" / "客户原有日志.log"
        log_sentinel.write_bytes(b"existing-log-must-survive")

        (install_root / "① 启动系统.bat").write_bytes(
            self.target.files["① 启动系统.bat"]
        )
        (program_root / "app.py").write_bytes(
            self.target.files["_程序文件/app.py"]
        )
        (program_root / "server.py").unlink()
        (program_root / "static" / "旧版残留.js").write_text(
            "stale", encoding="utf-8"
        )
        (program_root / updater.LEGACY_STATE_NAME).write_text(
            json.dumps({"Stage": "program_replaced"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (program_root / updater.LEGACY_LOCK_NAME).write_bytes(b"legacy-lock")
        legacy_rollback = (
            program_root
            / updater.LEGACY_ROLLBACK_NAME
            / "old-transaction"
            / "data"
            / "reservation.db"
        )
        legacy_rollback.parent.mkdir(parents=True)
        legacy_rollback.write_bytes(b"legacy-evidence-must-survive")

        original_copy2 = updater.shutil.copy2

        def reject_reopening_locked_legacy_file(
            source: object,
            destination: object,
            *args: object,
            **kwargs: object,
        ) -> object:
            if Path(source) == program_root / updater.LEGACY_LOCK_NAME:
                raise PermissionError(
                    "simulated Windows denial for a separately opened locked file"
                )
            return original_copy2(source, destination, *args, **kwargs)

        with mock.patch.object(
            updater.shutil,
            "copy2",
            side_effect=reject_reopening_locked_legacy_file,
        ):
            self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertEqual(_tree_bytes(data_root), data_before)
        self.assertEqual(
            legacy_rollback.read_bytes(), b"legacy-evidence-must-survive"
        )
        self.assertEqual(backup_sentinel.read_bytes(), b"do-not-delete")
        self.assertEqual(
            log_sentinel.read_bytes(), b"existing-log-must-survive"
        )
        self.assertFalse((program_root / updater.LEGACY_STATE_NAME).exists())
        self.assertFalse((program_root / updater.LEGACY_LOCK_NAME).exists())
        archives = list(
            (program_root / "logs").glob("V1.0.2旧升级残留_*")
        )
        self.assertEqual(len(archives), 1)
        self.assertTrue((archives[0] / updater.LEGACY_STATE_NAME).is_file())
        self.assertEqual(
            (archives[0] / updater.LEGACY_LOCK_NAME).read_bytes(),
            b"legacy-lock",
        )

    def test_partial_target_write_failure_strictly_stops_at_v101(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_root = program_root / "data"
        data_before = _tree_bytes(data_root)
        legacy_rollback = (
            program_root
            / updater.LEGACY_ROLLBACK_NAME
            / "old-transaction"
            / "program"
            / "server.py"
        )
        legacy_rollback.parent.mkdir(parents=True)
        legacy_rollback.write_bytes(b"old-server-evidence")

        original_atomic_write = updater._atomic_write
        injected = []

        def fail_once_during_target_server(
            path: Path, content: bytes
        ) -> None:
            if (
                path.name == "server.py"
                and path.parent.name == "_程序文件"
                and content == self.target.files["_程序文件/server.py"]
                and not injected
            ):
                injected.append(path)
                raise OSError("injected partial target overlay failure")
            original_atomic_write(path, content)

        with mock.patch.object(
            updater, "_atomic_write", side_effect=fail_once_during_target_server
        ):
            repair = updater.RepairUpdater(
                self.bundle,
                install_root,
                updater.PassiveSystemController(),
                validate_target=lambda _root, _data, _snapshot, _log: None,
            )
            with self.assertRaisesRegex(
                OSError, "partial target overlay failure"
            ):
                repair.run()

        self.assertEqual(len(injected), 1)
        self._assert_baseline_installed(install_root)
        self.assertEqual(_tree_bytes(data_root), data_before)
        self.assertEqual(
            legacy_rollback.read_bytes(), b"old-server-evidence"
        )
        state = json.loads(
            (program_root / updater.STATE_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["stage"], "baseline_rollback_complete")
        self.assertFalse((program_root / updater.LOCK_NAME).exists())

    def test_special_chinese_space_parentheses_and_ampersand_path(
        self,
    ) -> None:
        install_root = self._make_install(
            relative=(
                "D盘 360MoveData & 客户 (1)/"
                "Users/人社数据团002/Desktop/"
                "会议室预约系统-单位局域网版-最终 (1)"
            )
        )
        data_root = install_root / "_程序文件" / "data"
        data_before = _tree_bytes(data_root)

        self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertEqual(_tree_bytes(data_root), data_before)

    def test_unfinished_target_verified_state_can_resume_safely(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_root = program_root / "data"
        data_before = _tree_bytes(data_root)

        def crash_after_state_write(stage: str) -> None:
            if stage == "target_verified":
                raise RuntimeError("simulated abrupt process loss")

        interrupted = updater.RepairUpdater(
            self.bundle,
            install_root,
            updater.PassiveSystemController(),
            validate_target=lambda _root, _data, _snapshot, _log: None,
            fault_hook=crash_after_state_write,
        )
        interrupted._recover_after_failure = (  # type: ignore[method-assign]
            lambda _state, _snapshot, _error: None
        )
        with self.assertRaisesRegex(RuntimeError, "abrupt process loss"):
            interrupted.run()

        saved_state = json.loads(
            (program_root / updater.STATE_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(saved_state["stage"], "target_verified")
        self.assertEqual(
            (program_root / "版本.txt").read_text(encoding="utf-8").strip(),
            "1.0.1",
        )
        self.assertEqual(_tree_bytes(data_root), data_before)

        self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertEqual(_tree_bytes(data_root), data_before)
        self.assertFalse((program_root / updater.STATE_NAME).exists())
        self.assertFalse((program_root / updater.ROLLBACK_NAME).exists())

    def test_tampered_target_zip_is_rejected_before_install_changes(
        self,
    ) -> None:
        install_root = self._make_install()
        install_before = _tree_bytes(install_root)
        bundle_root = self.root / "待校验修复包"
        runtime = bundle_root / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "python.exe").write_bytes(b"runtime-python")
        (runtime / "pythonw.exe").write_bytes(b"runtime-pythonw")

        manifest: dict[str, object] = {
            "schema": 1,
            "release": updater.REPAIR_RELEASE,
        }
        for key, payload in (
            ("baseline", self.baseline),
            ("target", self.target),
        ):
            zip_name = f"{key}.zip"
            zip_path = bundle_root / zip_name
            with zipfile.ZipFile(
                zip_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for relative, content in sorted(payload.files.items()):
                    archive.writestr(relative, content)
            zip_bytes = zip_path.read_bytes()
            manifest[key] = {
                "version": payload.version,
                "file": zip_name,
                "sha256": hashlib.sha256(zip_bytes).hexdigest(),
                "size": len(zip_bytes),
                "files": updater._managed_records_from_files(payload.files),
            }

        runtime_records = updater._records_for_tree(runtime)
        manifest["runtime"] = {
            "tree_sha256": updater._tree_digest(runtime_records),
            "files": runtime_records,
        }
        (bundle_root / updater.TOOL_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        updater.Bundle.load(bundle_root)
        target_zip = bundle_root / "target.zip"
        target_zip.write_bytes(target_zip.read_bytes() + b"TAMPERED")

        with self.assertRaisesRegex(
            updater.UpdateError, "ZIP 大小不一致|SHA-256 不一致"
        ):
            updater.Bundle.load(bundle_root)

        self.assertEqual(_tree_bytes(install_root), install_before)

    def test_version_commit_handoff_never_reapplies_v101(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_before = _tree_bytes(program_root / "data")

        def crash_after_version_replace(event: str) -> None:
            if event == "version_file_replaced":
                raise RuntimeError("simulated commit handoff loss")

        interrupted = updater.RepairUpdater(
            self.bundle,
            install_root,
            updater.PassiveSystemController(),
            validate_target=lambda _root, _data, _snapshot, _log: None,
            fault_hook=crash_after_version_replace,
        )
        interrupted._recover_after_failure = (  # type: ignore[method-assign]
            lambda _state, _snapshot, _error: None
        )
        with self.assertRaisesRegex(RuntimeError, "commit handoff loss"):
            interrupted.run()

        state = json.loads(
            (program_root / updater.STATE_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["stage"], "healthcheck_passed")
        self._assert_target_installed(install_root)

        writes: list[str] = []
        original_write_payload = updater._write_managed_payload

        def record_payload(*args, **kwargs) -> None:
            payload = args[1]
            writes.append(payload.version)
            original_write_payload(*args, **kwargs)

        with mock.patch.object(
            updater, "_write_managed_payload", side_effect=record_payload
        ):
            self._run(install_root)

        self.assertEqual(writes, [])
        self._assert_target_installed(install_root)
        self.assertEqual(_tree_bytes(program_root / "data"), data_before)

    def test_committed_but_corrupt_target_fails_closed_without_downgrade(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_before = _tree_bytes(program_root / "data")

        def crash_after_version_replace(event: str) -> None:
            if event == "version_file_replaced":
                raise RuntimeError("simulated commit handoff loss")

        interrupted = updater.RepairUpdater(
            self.bundle,
            install_root,
            updater.PassiveSystemController(),
            validate_target=lambda _root, _data, _snapshot, _log: None,
            fault_hook=crash_after_version_replace,
        )
        interrupted._recover_after_failure = (  # type: ignore[method-assign]
            lambda _state, _snapshot, _error: None
        )
        with self.assertRaises(RuntimeError):
            interrupted.run()
        (program_root / "server.py").write_bytes(b"externally-corrupted")

        with self.assertRaisesRegex(updater.UpdateError, "受管程序.*不一致"):
            self._run(install_root)

        self.assertEqual(
            (program_root / "版本.txt").read_text(encoding="utf-8").strip(),
            "1.0.2",
        )
        self.assertEqual(
            (program_root / "server.py").read_bytes(),
            b"externally-corrupted",
        )
        self.assertEqual(_tree_bytes(program_root / "data"), data_before)

    def test_complete_state_can_finish_without_snapshot(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_before = _tree_bytes(program_root / "data")

        def crash_after_complete(event: str) -> None:
            if event == "complete":
                raise RuntimeError("simulated cleanup loss")

        interrupted = updater.RepairUpdater(
            self.bundle,
            install_root,
            updater.PassiveSystemController(),
            validate_target=lambda _root, _data, _snapshot, _log: None,
            fault_hook=crash_after_complete,
        )
        interrupted._recover_after_failure = (  # type: ignore[method-assign]
            lambda _state, _snapshot, _error: None
        )
        with self.assertRaisesRegex(RuntimeError, "cleanup loss"):
            interrupted.run()
        state = json.loads(
            (program_root / updater.STATE_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["stage"], "complete")
        shutil.rmtree(program_root / updater.ROLLBACK_NAME)

        self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertFalse((program_root / updater.STATE_NAME).exists())
        self.assertEqual(_tree_bytes(program_root / "data"), data_before)

    def test_stop_failure_restores_preflight_task_configuration(self) -> None:
        install_root = self._make_install()
        install_before = _tree_bytes(install_root)
        original = updater.TaskState(True, True, True, True)

        class FailingController(updater.PassiveSystemController):
            def stop_and_disable(self, install_root: Path) -> None:
                super().stop_and_disable(install_root)
                raise updater.UpdateError("simulated stop failure")

        controller = FailingController(original)
        repair = updater.RepairUpdater(
            self.bundle,
            install_root,
            controller,
            validate_target=lambda _root, _data, _snapshot, _log: None,
        )
        with self.assertRaisesRegex(updater.UpdateError, "stop failure"):
            repair.run()

        self.assertEqual(controller.restored, original)
        state = json.loads(
            (
                install_root / "_程序文件" / updater.STATE_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(state["stage"], "preflight")
        after = _tree_bytes(install_root)
        for relative, content in install_before.items():
            self.assertEqual(after[relative], content)

    def test_new_updater_holds_legacy_lock_for_whole_transaction(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_before = _tree_bytes(program_root / "data")
        legacy_lock = program_root / updater.LEGACY_LOCK_NAME

        with updater.ExclusiveLock(legacy_lock):
            repair = updater.RepairUpdater(
                self.bundle,
                install_root,
                updater.PassiveSystemController(),
                validate_target=lambda _root, _data, _snapshot, _log: None,
            )
            with self.assertRaisesRegex(
                updater.UpdateBusy, "旧 V1.0.2 升级器仍在运行"
            ):
                repair.run()

        self.assertEqual(_tree_bytes(program_root / "data"), data_before)
        self._assert_baseline_installed(install_root)

    def test_legacy_state_restores_original_task_setting(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        legacy_task = updater.TaskState(True, True, True, True)
        (program_root / updater.LEGACY_STATE_NAME).write_text(
            json.dumps(
                {
                    "Schema": 2,
                    "TransactionId": "a" * 32,
                    "PackageVersion": "1.0.2",
                    "Stage": "program_replaced",
                    "TaskExists": True,
                    "TaskEnabled": True,
                    "TaskWasRunning": True,
                    "WasRunning": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        controller = updater.PassiveSystemController(
            updater.TaskState(True, False, False, False)
        )

        self._run(install_root, controller=controller)

        self.assertEqual(controller.restored, legacy_task)

    def test_missing_start_bat_is_still_repairable(self) -> None:
        install_root = self._make_install()
        (install_root / "① 启动系统.bat").unlink()

        self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertTrue((install_root / "① 启动系统.bat").is_file())

    def test_tool_inside_program_directory_is_rejected(self) -> None:
        install_root = self._make_install()
        unsafe_tool = install_root / "_程序文件" / "data" / "修复工具"
        unsafe_tool.mkdir()
        with self.assertRaisesRegex(
            updater.UpdateError, "不能放在安装目录的.*_程序文件"
        ):
            updater._assert_tool_location(unsafe_tool, install_root)

    def test_new_data_after_failed_v101_fallback_becomes_authoritative(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"

        def reject_target(_root, _data, _snapshot, _log) -> None:
            raise updater.UpdateError("simulated target rejection")

        with self.assertRaisesRegex(updater.UpdateError, "target rejection"):
            self._run(install_root, validate_target=reject_target)
        self._assert_baseline_installed(install_root)

        new_record = program_root / "data" / "离线期间新增记录.bin"
        new_record.write_bytes(b"new-authoritative-data")
        database = program_root / "data" / "reservation.db"
        connection = sqlite3.connect(str(database))
        try:
            connection.execute(
                "INSERT INTO reservations(id, user_id, subject) "
                "VALUES (?, ?, ?)",
                (8, 1, "V1.0.1 回退后新增预约"),
            )
            connection.commit()
        finally:
            connection.close()
        data_before_retry = _tree_bytes(program_root / "data")

        self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertEqual(
            _tree_bytes(program_root / "data"), data_before_retry
        )
        connection = sqlite3.connect(str(database))
        try:
            subject = connection.execute(
                "SELECT subject FROM reservations WHERE id = 8"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(subject, ("V1.0.1 回退后新增预约",))
        permanent_backups = sorted(
            (program_root / "backups").glob("pre_v102_repair_*.db")
        )
        self.assertEqual(len(permanent_backups), 2)
        connection = sqlite3.connect(str(permanent_backups[-1]))
        try:
            backed_up_subject = connection.execute(
                "SELECT subject FROM reservations WHERE id = 8"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            backed_up_subject, ("V1.0.1 回退后新增预约",)
        )

    def test_insufficient_space_stops_before_service_or_state_changes(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        install_before = _tree_bytes(install_root)
        controller = updater.PassiveSystemController()
        disk_usage = shutil._ntuple_diskusage(total=1, used=1, free=0)

        with mock.patch.object(
            updater.shutil, "disk_usage", return_value=disk_usage
        ):
            repair = updater.RepairUpdater(
                self.bundle,
                install_root,
                controller,
                validate_target=lambda _root, _data, _snapshot, _log: None,
            )
            with self.assertRaisesRegex(
                updater.UpdateError, "剩余空间不足"
            ):
                repair.run()

        self.assertEqual(controller.stop_calls, 0)
        self.assertIsNone(controller.restored)
        self.assertFalse((program_root / updater.STATE_NAME).exists())
        after = _tree_bytes(install_root)
        for relative, content in install_before.items():
            self.assertEqual(after[relative], content)

    def test_space_estimate_includes_large_existing_managed_tree(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        stale = program_root / "static" / "旧版超大残留.bin"
        stale.write_bytes(b"x" * (4 * 1024 * 1024))
        data_root = program_root / "data"
        data_size = updater._tree_size(data_root, "test data")
        database_size = (data_root / "reservation.db").stat().st_size
        runtime_size = sum(
            int(record["size"]) for record in self.bundle.runtime_records
        )
        program_size = max(
            sum(int(record["size"]) for record in self.baseline.records),
            sum(int(record["size"]) for record in self.target.records),
        )
        old_estimate = (
            data_size * 2
            + database_size
            + runtime_size
            + program_size * 3
            + 128 * 1024 * 1024
        )
        controller = updater.PassiveSystemController()
        disk_usage = shutil._ntuple_diskusage(
            total=old_estimate + 1,
            used=0,
            free=old_estimate + 1,
        )

        with mock.patch.object(
            updater.shutil, "disk_usage", return_value=disk_usage
        ):
            with self.assertRaisesRegex(
                updater.UpdateError, "剩余空间不足"
            ):
                self._run(install_root, controller=controller)

        self.assertEqual(controller.stop_calls, 0)
        self.assertFalse((program_root / updater.STATE_NAME).exists())

    def test_corrupt_runtime_file_is_repaired_as_frozen_runtime(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        runtime = program_root / "runtime"
        runtime.write_bytes(b"broken-runtime-file")
        data_before = _tree_bytes(program_root / "data")

        self._run(install_root)

        self.assertTrue(runtime.is_dir())
        updater._assert_record_sets_equal(
            self.bundle.runtime_records,
            updater._records_for_tree(runtime),
            "修复后的 runtime",
        )
        self.assertEqual(_tree_bytes(program_root / "data"), data_before)

    def test_exact_v102_rerun_is_a_true_noop(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        self._run(install_root)
        data_before = _tree_bytes(program_root / "data")
        backups_before = _tree_bytes(program_root / "backups")
        controller = updater.PassiveSystemController(
            updater.TaskState(True, True, True, True)
        )

        with mock.patch.object(
            updater,
            "_write_managed_payload",
            side_effect=AssertionError("exact rerun must not rewrite payload"),
        ):
            self._run(install_root, controller=controller)

        self.assertEqual(controller.stop_calls, 0)
        self.assertIsNone(controller.restored)
        self.assertEqual(_tree_bytes(program_root / "data"), data_before)
        self.assertEqual(
            _tree_bytes(program_root / "backups"), backups_before
        )

    def test_exact_v102_rerun_retires_only_known_broken_bat(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        self._run(install_root)
        broken = install_root / "升级到V1.0.2(1).bat"
        unrelated = install_root / "升级到V1.0.2-客户保留.bat"
        broken.write_bytes(b"known-broken-v102-package")
        unrelated.write_bytes(b"customer-owned-different-package")
        staging = program_root / f".static.repair-{'d' * 32}.new"
        staging.mkdir()
        (staging / "temporary.js").write_bytes(b"staging")
        broken_sha256 = hashlib.sha256(broken.read_bytes()).hexdigest()
        data_before = _tree_bytes(program_root / "data")
        controller = updater.PassiveSystemController()

        with mock.patch.object(
            updater,
            "BROKEN_V102_PACKAGE_SHA256",
            broken_sha256,
        ):
            self._run(install_root, controller=controller)

        self.assertFalse(broken.exists())
        self.assertTrue(unrelated.is_file())
        self.assertEqual(
            unrelated.read_bytes(), b"customer-owned-different-package"
        )
        archives = list(
            (program_root / "backups").glob("旧V1.0.2升级文件_*")
        )
        self.assertEqual(len(archives), 1)
        self.assertEqual(
            (archives[0] / broken.name).read_bytes(),
            b"known-broken-v102-package",
        )
        self.assertFalse(staging.exists())
        self.assertEqual(controller.stop_calls, 0)
        self.assertIsNone(controller.restored)
        self.assertEqual(_tree_bytes(program_root / "data"), data_before)

    def test_legacy_v101_committed_state_continues_to_v102(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        (program_root / updater.LEGACY_STATE_NAME).write_text(
            json.dumps(
                {
                    "Schema": 2,
                    "TransactionId": "b" * 32,
                    "PackageVersion": "1.0.1",
                    "Stage": "version_committed",
                    "TaskExists": False,
                    "TaskEnabled": False,
                    "TaskWasRunning": False,
                    "WasRunning": False,
                }
            ),
            encoding="utf-8",
        )

        self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertFalse((program_root / updater.LEGACY_STATE_NAME).exists())

    def test_legacy_v102_committed_state_uses_durable_cleanup_only(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        _write_files(install_root, dict(self.target.files))
        self._install_frozen_runtime(install_root)
        (program_root / updater.LEGACY_STATE_NAME).write_text(
            json.dumps(
                {
                    "Schema": 2,
                    "TransactionId": "c" * 32,
                    "PackageVersion": "1.0.2",
                    "Stage": "version_committed",
                    "TaskExists": True,
                    "TaskEnabled": True,
                    "TaskWasRunning": True,
                    "WasRunning": True,
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(
            updater,
            "_write_managed_payload",
            side_effect=AssertionError("committed legacy must not downgrade"),
        ):
            self._run(install_root)

        self._assert_target_installed(install_root)
        self.assertFalse((program_root / updater.STATE_NAME).exists())
        self.assertFalse((program_root / updater.LEGACY_STATE_NAME).exists())

    def test_target_committed_with_corrupt_runtime_fails_closed(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        _write_files(install_root, dict(self.target.files))
        runtime = self._install_frozen_runtime(install_root)
        controller = updater.PassiveSystemController(
            updater.TaskState(True, True, True, True)
        )
        repair = updater.RepairUpdater(
            self.bundle,
            install_root,
            controller,
            validate_target=lambda _root, _data, _snapshot, _log: None,
        )
        state = repair._new_state(controller.state)
        state["stage"] = "target_committed"
        updater._write_state(program_root / updater.STATE_NAME, state)
        (runtime / "python311.dll").write_bytes(b"corrupt-after-commit")

        with self.assertRaisesRegex(
            updater.UpdateError, "runtime.*不一致"
        ):
            repair.run()

        self.assertIsNone(controller.restored)
        self.assertEqual(
            (program_root / "版本.txt").read_text(encoding="utf-8").strip(),
            "1.0.2",
        )

    def test_legacy_committed_with_corrupt_runtime_fails_closed(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        _write_files(install_root, dict(self.target.files))
        runtime = self._install_frozen_runtime(install_root)
        legacy_state = program_root / updater.LEGACY_STATE_NAME
        legacy_state.write_text(
            json.dumps(
                {
                    "Schema": 2,
                    "TransactionId": "e" * 32,
                    "PackageVersion": "1.0.2",
                    "Stage": "version_committed",
                    "TaskExists": True,
                    "TaskEnabled": True,
                    "TaskWasRunning": True,
                    "WasRunning": True,
                }
            ),
            encoding="utf-8",
        )
        (runtime / "python311.dll").write_bytes(b"corrupt-legacy-runtime")
        controller = updater.PassiveSystemController(
            updater.TaskState(True, True, True, True)
        )

        with self.assertRaisesRegex(
            updater.UpdateError, "runtime.*不一致"
        ):
            self._run(install_root, controller=controller)

        self.assertTrue(legacy_state.is_file())
        self.assertFalse((program_root / updater.STATE_NAME).exists())
        self.assertEqual(controller.stop_calls, 0)
        self.assertIsNone(controller.restored)
        self.assertEqual(
            (program_root / "版本.txt").read_text(encoding="utf-8").strip(),
            "1.0.2",
        )

    def test_rollback_failure_keeps_task_disabled(self) -> None:
        install_root = self._make_install()
        controller = updater.PassiveSystemController(
            updater.TaskState(True, True, True, True)
        )
        original_write = updater._write_managed_payload
        baseline_writes = 0

        def fail_target_and_recovery(*args, **kwargs) -> None:
            nonlocal baseline_writes
            payload = args[1]
            if payload.version == updater.BASELINE_VERSION:
                baseline_writes += 1
                if baseline_writes > 1:
                    raise OSError("simulated baseline recovery failure")
            if payload.version == updater.TARGET_VERSION:
                raise OSError("simulated target failure")
            original_write(*args, **kwargs)

        with mock.patch.object(
            updater,
            "_write_managed_payload",
            side_effect=fail_target_and_recovery,
        ):
            with self.assertRaisesRegex(OSError, "target failure"):
                self._run(install_root, controller=controller)

        self.assertIsNone(controller.restored)
        self.assertEqual(controller.stop_calls, 1)

    def test_managed_symlink_is_rejected_before_stop_or_state(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        server = program_root / "server.py"
        outside = self.root / "outside-server.py"
        outside.write_bytes(b"outside")
        server.unlink()
        server.symlink_to(outside)
        controller = updater.PassiveSystemController()
        repair = updater.RepairUpdater(
            self.bundle,
            install_root,
            controller,
            validate_target=lambda _root, _data, _snapshot, _log: None,
        )

        with self.assertRaisesRegex(
            updater.UpdateError, "受管文件不是普通文件"
        ):
            repair.run()

        self.assertEqual(controller.stop_calls, 0)
        self.assertFalse((program_root / updater.STATE_NAME).exists())
        self.assertTrue(server.is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_dangling_managed_symlink_is_rejected_before_stop(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        server = program_root / "server.py"
        server.unlink()
        server.symlink_to(self.root / "missing-outside-server.py")
        controller = updater.PassiveSystemController()

        with self.assertRaisesRegex(
            updater.UpdateError, "受管文件不是普通文件"
        ):
            self._run(install_root, controller=controller)

        self.assertEqual(controller.stop_calls, 0)
        self.assertFalse((program_root / updater.STATE_NAME).exists())
        self.assertTrue(server.is_symlink())

    def test_dangling_runtime_and_lock_links_are_rejected(self) -> None:
        for relative, error_pattern in (
            ("runtime", "runtime 是链接"),
            (updater.LOCK_NAME, "修复锁文件不能是链接"),
        ):
            with self.subTest(relative=relative):
                install_root = self._make_install(
                    relative=f"悬空链接-{relative}"
                )
                program_root = install_root / "_程序文件"
                link = program_root / relative
                link.symlink_to(self.root / f"missing-{relative}")
                controller = updater.PassiveSystemController()

                with self.assertRaisesRegex(
                    updater.UpdateError, error_pattern
                ):
                    self._run(install_root, controller=controller)

                self.assertEqual(controller.stop_calls, 0)
                self.assertTrue(link.is_symlink())

    def test_nested_data_symlink_is_rejected_before_stop_or_state(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_root = program_root / "data"
        outside = self.root / "outside-customer-data.bin"
        outside.write_bytes(b"outside-data-must-survive")
        link = data_root / "客户附件链接.bin"
        link.symlink_to(outside)
        controller = updater.PassiveSystemController()

        with self.assertRaisesRegex(
            updater.UpdateError, "客户 data.*链接|客户 data.*特殊文件"
        ):
            self._run(install_root, controller=controller)

        self.assertEqual(controller.stop_calls, 0)
        self.assertFalse((program_root / updater.STATE_NAME).exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside-data-must-survive")

    def test_corrupt_repair_state_fails_before_stop_and_preserves_data(
        self,
    ) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        data_before = _tree_bytes(program_root / "data")
        state_path = program_root / updater.STATE_NAME
        state_path.write_bytes(b"{not-json")
        controller = updater.PassiveSystemController()

        with self.assertRaisesRegex(
            updater.UpdateError, "修复状态文件损坏"
        ):
            self._run(install_root, controller=controller)

        self.assertEqual(controller.stop_calls, 0)
        self.assertIsNone(controller.restored)
        self.assertEqual(state_path.read_bytes(), b"{not-json")
        self.assertEqual(_tree_bytes(program_root / "data"), data_before)

    def test_runtime_swap_failure_restores_original_runtime(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        runtime = program_root / "runtime"
        runtime.mkdir()
        (runtime / "python.exe").write_bytes(b"customer-python")
        (runtime / "pythonw.exe").write_bytes(b"customer-pythonw")
        original_runtime = _tree_bytes(runtime)
        original_replace = updater.os.replace

        def fail_staged_runtime(source, destination) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                ".runtime.repair-" in source_path.name
                and source_path.name.endswith(".new")
                and destination_path.resolve(strict=False)
                == runtime.resolve(strict=False)
            ):
                raise OSError("simulated staged runtime rename failure")
            original_replace(source, destination)

        with mock.patch.object(
            updater.os, "replace", side_effect=fail_staged_runtime
        ):
            with self.assertRaisesRegex(
                OSError, "staged runtime rename failure"
            ):
                self._run(install_root)

        self.assertTrue(runtime.is_dir())
        self.assertEqual(_tree_bytes(runtime), original_runtime)

    def test_double_runtime_restore_failure_keeps_task_disabled(self) -> None:
        install_root = self._make_install()
        program_root = install_root / "_程序文件"
        runtime = program_root / "runtime"
        runtime.mkdir()
        (runtime / "python.exe").write_bytes(b"customer-python")
        (runtime / "pythonw.exe").write_bytes(b"customer-pythonw")
        data_before = _tree_bytes(program_root / "data")
        controller = updater.PassiveSystemController(
            updater.TaskState(True, True, True, True)
        )
        repair = updater.RepairUpdater(
            self.bundle,
            install_root,
            controller,
            validate_target=lambda _root, _data, _snapshot, _log: None,
        )
        original_replace = updater.os.replace

        def fail_install_and_restore(source, destination) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            is_runtime_destination = (
                destination_path.resolve(strict=False)
                == runtime.resolve(strict=False)
            )
            if (
                is_runtime_destination
                and ".runtime.repair-" in source_path.name
                and source_path.name.endswith(".new")
            ):
                raise OSError("simulated staged runtime rename failure")
            if (
                is_runtime_destination
                and source_path.name == "original-runtime"
            ):
                raise PermissionError(
                    "simulated original runtime restore failure"
                )
            original_replace(source, destination)

        with mock.patch.object(
            updater.os, "replace", side_effect=fail_install_and_restore
        ):
            with self.assertRaisesRegex(
                OSError, "staged runtime rename failure"
            ):
                repair.run()

        self.assertFalse(runtime.exists())
        self.assertEqual(controller.stop_calls, 1)
        self.assertIsNone(controller.restored)
        self.assertEqual(_tree_bytes(program_root / "data"), data_before)
        state = json.loads(
            (program_root / updater.STATE_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["stage"], "snapshot_ready")


if __name__ == "__main__":
    unittest.main()
