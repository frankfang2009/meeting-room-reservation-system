from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from v2.installer.build_runtime import (
    PYTHON_SOURCE_SHA256,
    PYTHON_SOURCE_URL,
    PYTHON_VERSION,
    RuntimeBuildError,
    build_runtime,
)
from v2.installer.installer_core import RUNTIME_PTH_LINES, validate_runtime_supply_chain
from v2.installer.tests.helpers import _synthetic_amd64_pe


class RuntimeBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _wheel(self, name: str, version: str, module: str) -> Path:
        path = self.root / "wheelhouse" / f"{module}-{version}-py3-none-any.whl"
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

    def _inputs(self) -> tuple[Path, Path, Path, str]:
        embed = self.root / "python-3.13.7-embed-amd64.zip"
        with zipfile.ZipFile(embed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for executable in ("python.exe", "pythonw.exe", "python313.dll"):
                archive.writestr(executable, _synthetic_amd64_pe())
            archive.writestr("python313.zip", b"synthetic stdlib")
            archive.writestr("python313._pth", "python313.zip\n.\nimport site\n")
            archive.writestr("LICENSE.txt", "Synthetic CPython license\n")
        flask = self._wheel("Flask", "3.1.3", "flask")
        waitress = self._wheel("waitress", "3.0.2", "waitress")
        lock = self.root / "requirements-win-amd64.lock"
        lock.write_text(
            "Flask==3.1.3 \\\n"
            f"    --hash=sha256:{hashlib.sha256(flask.read_bytes()).hexdigest()}\n"
            "waitress==3.0.2 \\\n"
            f"    --hash=sha256:{hashlib.sha256(waitress.read_bytes()).hexdigest()}\n",
            encoding="utf-8",
        )
        return embed, self.root / "wheelhouse", lock, hashlib.sha256(embed.read_bytes()).hexdigest()

    def test_builds_isolated_runtime_and_real_supply_chain_materials(self) -> None:
        embed, wheelhouse, lock, embed_sha = self._inputs()
        output = self.root / "runtime"
        result = build_runtime(
            embed,
            wheelhouse,
            lock,
            output,
            _python_version="3.13.7",
            _python_source_url=(
                "https://www.python.org/ftp/python/3.13.7/"
                "python-3.13.7-embed-amd64.zip"
            ),
            _expected_python_sha256=embed_sha,
            _test_fixture=True,
        )
        self.assertEqual(result.component_count, 2)
        self.assertEqual(
            tuple(
                line.strip()
                for line in (output / "python313._pth").read_text().splitlines()
                if line.strip()
            ),
            RUNTIME_PTH_LINES,
        )
        self.assertEqual(
            (output / "supply-chain" / "requirements.lock").read_bytes(),
            lock.read_bytes(),
        )
        provenance = validate_runtime_supply_chain(output, _test_fixture=True)
        self.assertEqual(provenance["python"]["source_sha256"], embed_sha)
        self.assertEqual(len(provenance["components"]), 2)
        sbom = json.loads((output / "supply-chain" / "sbom.cdx.json").read_text())
        self.assertEqual(len(sbom["components"]), 3)
        notices = (output / "supply-chain" / "THIRD-PARTY-NOTICES.txt").read_text()
        self.assertIn("Synthetic CPython license", notices)
        self.assertIn("Synthetic MIT license", notices)

    def test_rejects_wrong_upstream_or_wheel_hash_without_output(self) -> None:
        embed, wheelhouse, lock, embed_sha = self._inputs()
        output = self.root / "runtime-wrong-python"
        with self.assertRaises(RuntimeBuildError):
            build_runtime(
                embed,
                wheelhouse,
                lock,
                output,
                _expected_python_sha256="0" * 64,
                _test_fixture=True,
            )
        self.assertFalse(output.exists())

        wheel = next(wheelhouse.glob("flask-*.whl"))
        wheel.write_bytes(wheel.read_bytes() + b"tamper")
        output = self.root / "runtime-wrong-wheel"
        with self.assertRaises(RuntimeBuildError):
            build_runtime(
                embed,
                wheelhouse,
                lock,
                output,
                _python_version="3.13.7",
                _python_source_url=(
                    "https://www.python.org/ftp/python/3.13.7/"
                    "python-3.13.7-embed-amd64.zip"
                ),
                _expected_python_sha256=embed_sha,
                _test_fixture=True,
            )
        self.assertFalse(output.exists())

    def test_production_constants_pin_canonical_cpython_artifact(self) -> None:
        self.assertEqual(PYTHON_VERSION, "3.13.14")
        self.assertEqual(
            PYTHON_SOURCE_URL,
            "https://www.python.org/ftp/python/3.13.14/"
            "python-3.13.14-embed-amd64.zip",
        )
        self.assertEqual(
            PYTHON_SOURCE_SHA256,
            "90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907",
        )

    def test_formal_builder_rejects_replaced_python_identity(self) -> None:
        embed, wheelhouse, lock, embed_sha = self._inputs()
        with self.assertRaises(RuntimeBuildError):
            build_runtime(
                embed,
                wheelhouse,
                lock,
                self.root / "formal-runtime",
                _python_version="3.13.7",
                _python_source_url=(
                    "https://www.python.org/ftp/python/3.13.7/"
                    "python-3.13.7-embed-amd64.zip"
                ),
                _expected_python_sha256=embed_sha,
            )


if __name__ == "__main__":
    unittest.main()
