from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from v2.installer.build_macos_dmg import staging_from_zip
from v2.installer.build_macos_package import (
    EDITION_CONTENT,
    TOP_FOLDER,
    MacPackageBuildError,
    build_macos_package,
)
from v2.installer.build_runtime_macos import (
    MacRuntimeBuildError,
    build_macos_runtime,
)


def _wheel(root: Path, name: str, version: str, module: str) -> Path:
    path = root / "wheelhouse" / f"{module}-{version}-py3-none-any.whl"
    path.parent.mkdir(exist_ok=True)
    dist_info = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{module}/__init__.py", "# synthetic wheel\n")
        archive.writestr(
            f"{dist_info}/METADATA",
            "Metadata-Version: 2.4\n"
            f"Name: {name}\nVersion: {version}\n"
            "License-Expression: MIT\n"
            "Project-URL: Source, https://example.invalid/source\n\n",
        )
        archive.writestr(f"{dist_info}/licenses/LICENSE.txt", "Synthetic MIT license\n")
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\n")
    return path


class MacOSRuntimeBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def _tarball(self) -> Path:
        tarball = self.root / "cpython.tar.gz"
        with tarfile.open(tarball, "w:gz") as archive:
            def add(name: str, data: bytes, *, mode: int = 0o644) -> None:
                info = tarfile.TarInfo(f"python/{name}")
                info.size = len(data)
                info.mode = mode
                import io

                archive.addfile(info, io.BytesIO(data))

            add("BUILD", b"20260718\n")
            add("bin/python3.13", b"#!/script\n", mode=0o755)
            add("bin/pip3", b"pip shim\n", mode=0o755)
            add("bin/pydoc3", b"pydoc shim\n", mode=0o755)
            add("lib/python3.13/LICENSE.txt", b"Synthetic PSF license\n")
            add("lib/python3.13/site-packages/README.txt", b"synthetic\n")
            add("lib/pkgconfig/python3.pc", b"synthetic\n")
            add("share/man/man1/python3.1", b"synthetic man\n")
            # bin/python 与 bin/python3 是保留并应被物化的符号链接。
            for link_name in ("bin/python", "bin/python3"):
                info = tarfile.TarInfo(f"python/{link_name}")
                info.type = tarfile.SYMTYPE
                info.linkname = "python3.13"
                info.mode = 0o755
                archive.addfile(info)
        return tarball

    def _inputs(self) -> tuple[Path, Path, Path]:
        tarball = self._tarball()
        flask = _wheel(self.root, "Flask", "3.1.3", "flask")
        lock = self.root / "requirements-macos-arm64.lock"
        lock.write_text(
            "Flask==3.1.3 \\\n"
            f"    --hash=sha256:{hashlib.sha256(flask.read_bytes()).hexdigest()}\n",
            encoding="utf-8",
        )
        return tarball, self.root / "wheelhouse", lock

    def test_builds_slimmed_runtime_with_materialized_bin_links(self) -> None:
        tarball, wheelhouse, lock = self._inputs()
        output = self.root / "runtime"
        result = build_macos_runtime(tarball, wheelhouse, lock, output, _test_fixture=True)
        self.assertEqual(result.component_count, 1)
        for excluded in (
            "bin/pip3",
            "bin/pydoc3",
            "lib/pkgconfig",
            "share",
            "BUILD",
        ):
            self.assertFalse((output / excluded).exists(), excluded)
        for kept in ("bin/python3.13", "bin/python", "bin/python3"):
            self.assertTrue((output / kept).is_file(), kept)
            self.assertFalse((output / kept).is_symlink(), kept)
        self.assertTrue((output / "lib/python3.13/site-packages/flask/__init__.py").is_file())
        supply_chain = output / "supply-chain"
        for name in (
            "requirements-macos-arm64.lock",
            "sbom.cdx.json",
            "THIRD-PARTY-NOTICES.txt",
            "runtime-provenance.json",
        ):
            self.assertTrue((supply_chain / name).is_file(), name)
        provenance = json.loads(
            (supply_chain / "runtime-provenance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(provenance["python"]["architecture"], "arm64")
        self.assertEqual(provenance["python"]["build_tag"], "20260718")
        self.assertEqual(provenance["components"], {"Flask": "3.1.3"})
        self.assertTrue(provenance["runtime"]["tree_sha256"])

    def test_rejects_tarball_sha_mismatch_in_production_mode(self) -> None:
        tarball, wheelhouse, lock = self._inputs()
        with self.assertRaises(MacRuntimeBuildError):
            build_macos_runtime(tarball, wheelhouse, lock, self.root / "runtime")

    def test_deterministic_across_two_builds(self) -> None:
        import subprocess

        outputs = []
        for index in range(2):
            tarball, wheelhouse, lock = self._inputs()
            output = self.root / f"runtime-{index}"
            build_macos_runtime(tarball, wheelhouse, lock, output, _test_fixture=True)
            outputs.append(output)
        left, right = outputs
        comparison = subprocess.run(
            ["diff", "-r", str(left), str(right)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(comparison.returncode, 0, comparison.stdout + comparison.stderr)


class MacOSPackageBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def _fixture(self) -> dict[str, Path]:
        backend = self.root / "backend"
        (backend / "v2app" / "api").mkdir(parents=True)
        for name in (
            "service.py",
            "server.py",
            "backup.py",
            "restore.py",
            "requirements.txt",
            "requirements-macos-arm64.lock",
        ):
            (backend / name).write_text("# synthetic\n", encoding="utf-8")
        (backend / "v2app" / "__init__.py").write_text("# synthetic\n", encoding="utf-8")
        (backend / "v2app" / "api" / "__init__.py").write_text("# synthetic\n", encoding="utf-8")
        (self.root / "VERSION").write_text("2.3.0\n", encoding="utf-8")
        frontend_dir = self.root / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": "fixture",
                    "lockfileVersion": 3,
                    "requires": True,
                    "packages": {
                        "": {"name": "fixture-app", "version": "0.0.0", "license": "Apache-2.0"},
                        "node_modules/react": {
                            "version": "19.2.4",
                            "license": "MIT",
                            "integrity": "sha512-synthetic",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        frontend_dist = self.root / "dist"
        frontend_dist.mkdir()
        (frontend_dist / "index.html").write_text("<html></html>", encoding="utf-8")
        runtime = self.root / "runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin" / "python3.13").write_text("# synthetic", encoding="utf-8")
        site_packages = runtime / "lib" / "python3.13" / "site-packages"
        (site_packages / "flask").mkdir(parents=True)
        (site_packages / "flask" / "__init__.py").write_text("# synthetic\n", encoding="utf-8")
        supply = runtime / "supply-chain"
        supply.mkdir()
        for name in (
            "requirements-macos-arm64.lock",
            "sbom.cdx.json",
            "THIRD-PARTY-NOTICES.txt",
            "runtime-provenance.json",
        ):
            (supply / name).write_text("{}\n", encoding="utf-8")
        return {
            "backend_root": backend,
            "frontend_dist": frontend_dist,
            "runtime_root": runtime,
            "templates_dir": Path(__file__).resolve().parent.parent
            / "payload_templates_macos",
        }

    def test_builds_reproducible_zip_with_edition_and_permissions(self) -> None:
        fixture = self._fixture()
        outputs = []
        for index in range(2):
            output_dir = self.root / f"pkg{index}"
            summary = build_macos_package(
                backend_root=fixture["backend_root"],
                frontend_dist=fixture["frontend_dist"],
                runtime_root=fixture["runtime_root"],
                templates_dir=fixture["templates_dir"],
                output_dir=output_dir,
            )
            outputs.append(output_dir / Path(summary["artifact"]).name)
            self.assertEqual(summary["version"], "2.3.0")
        self.assertEqual(
            outputs[0].read_bytes(), outputs[1].read_bytes(), "两次构建必须字节一致"
        )
        artifact = outputs[0]
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
            self.assertIn(f"{TOP_FOLDER}/启动.command", names)
            self.assertIn(f"{TOP_FOLDER}/app/EDITION", names)
            self.assertIn(f"{TOP_FOLDER}/app/frontend-production-components.json", names)
            self.assertIn(f"{TOP_FOLDER}/runtime/bin/python3.13", names)
            for forbidden in ("data/", "backups/", "logs/"):
                self.assertFalse(
                    any(name.startswith(f"{TOP_FOLDER}/{forbidden}") for name in names)
                )
            edition = archive.read(f"{TOP_FOLDER}/app/EDITION").decode("utf-8")
            self.assertEqual(edition, EDITION_CONTENT)
            for launcher in ("启动.command", "停止.command"):
                info = archive.getinfo(f"{TOP_FOLDER}/{launcher}")
                mode = (info.external_attr >> 16) & 0o777
                self.assertEqual(mode, 0o755, launcher)
            python_info = archive.getinfo(f"{TOP_FOLDER}/runtime/bin/python3.13")
            self.assertEqual((python_info.external_attr >> 16) & 0o777, 0o755)
            for name in names:
                info = archive.getinfo(name)
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0), name)
        sidecars = list(artifact.parent.iterdir())
        for suffix in (
            ".sha256.txt",
            ".manifest.json",
            ".sbom.cdx.json",
            ".THIRD-PARTY-NOTICES.txt",
            ".runtime-provenance.json",
        ):
            self.assertTrue(
                any(sidecar.name.endswith(suffix) for sidecar in sidecars), suffix
            )
        manifest = json.loads(
            (artifact.parent / (artifact.name + ".manifest.json")).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["platform"], "macos-arm64")
        self.assertEqual(manifest["distribution_channel"], "GitHub Release")
        self.assertIn("RELEASE-CHECKLIST", manifest["release_gate"])
        self.assertNotIn("formal_external_release_allowed", manifest)
        latest = json.loads(
            (artifact.parent / "latest-macos.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            latest,
            {
                "product": "meeting-room-reservation-system-v2",
                "channel": "macos-selfhost",
                "version": "2.3.0",
                "tag": "v2.3.0",
            },
        )

    def test_staging_from_zip_restores_top_folder_and_exec_bits(self) -> None:
        fixture = self._fixture()
        output_dir = self.root / "pkg"
        summary = build_macos_package(
            backend_root=fixture["backend_root"],
            frontend_dist=fixture["frontend_dist"],
            runtime_root=fixture["runtime_root"],
            templates_dir=fixture["templates_dir"],
            output_dir=output_dir,
        )
        staging = staging_from_zip(
            Path(summary["artifact"]), self.root / "dmg-staging"
        )
        self.assertEqual(staging.name, TOP_FOLDER)
        self.assertTrue((staging / "app" / "service.py").is_file())
        launcher = staging / "启动.command"
        self.assertTrue(launcher.stat().st_mode & 0o111, "启动.command 应保留可执行位")

    def test_rejects_missing_version_or_static(self) -> None:
        fixture = self._fixture()
        (fixture["frontend_dist"] / "index.html").unlink()
        with self.assertRaises(MacPackageBuildError):
            build_macos_package(
                backend_root=fixture["backend_root"],
                frontend_dist=fixture["frontend_dist"],
                runtime_root=fixture["runtime_root"],
                templates_dir=fixture["templates_dir"],
                output_dir=self.root / "out",
            )


if __name__ == "__main__":
    unittest.main()
