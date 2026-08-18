from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Tuple

from v2.installer.build_package import ARTIFACT_NAME, BuildResult, build_package
from v2.installer.installer_core import Bundle, records_for_tree, tree_digest


def _synthetic_amd64_pe() -> bytes:
    content = bytearray(128)
    content[:2] = b"MZ"
    content[60:64] = (64).to_bytes(4, "little")
    content[64:68] = b"PE\0\0"
    content[68:70] = (0x8664).to_bytes(2, "little")
    return bytes(content)


def refresh_runtime_mapping(runtime: Path) -> None:
    provenance_path = runtime / "supply-chain" / "runtime-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    runtime_records = tuple(
        record
        for record in records_for_tree(runtime)
        if record["path"] != "supply-chain/runtime-provenance.json"
    )
    provenance["runtime"] = {
        "tree_sha256": tree_digest(runtime_records),
        "files": list(runtime_records),
    }
    provenance_path.write_text(
        json.dumps(provenance, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def create_inputs(root: Path) -> Tuple[Path, Path]:
    payload = root / "payload"
    program = payload / "_程序文件"
    app = program / "app"
    (app / "v2app").mkdir(parents=True)
    (app / "static" / "assets").mkdir(parents=True)
    (app / "service.py").write_text(
        "# synthetic service fixture\n",
        encoding="utf-8",
    )
    (app / "v2app" / "__init__.py").write_text("# fixture\n", encoding="utf-8")
    (app / "static" / "index.html").write_text(
        "<!doctype html><title>V2 fixture</title>\n",
        encoding="utf-8",
    )
    (app / "static" / "assets" / "app.js").write_text(
        "console.log('fixture');\n",
        encoding="utf-8",
    )
    integrity = "sha512-AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0+Pw=="
    (app / "frontend-production-components.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "application": {"name": "meeting-room-v2-frontend", "version": "2.2.2"},
                "packageLock": {"file": "package-lock.json", "sha256": "3" * 64},
                "components": [
                    {
                        "name": "react",
                        "version": "19.2.0",
                        "integrity": integrity,
                        "license": "MIT",
                        "resolved": "https://registry.npmjs.org/react/-/react-19.2.0.tgz",
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (payload / "使用说明.txt").write_text("fixture payload\n", encoding="utf-8")

    runtime = root / "runtime"
    site_packages = runtime / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(_synthetic_amd64_pe())
    (runtime / "pythonw.exe").write_bytes(_synthetic_amd64_pe())
    (runtime / "python313.zip").write_bytes(b"synthetic-stdlib")
    (runtime / "python313._pth").write_text(
        "python313.zip\n.\nLib\\site-packages\n..\\app\n",
        encoding="utf-8",
    )
    (site_packages / "fixture.txt").write_text(
        "fixture runtime\n",
        encoding="utf-8",
    )
    for distribution, module_name, version in (
        ("Flask", "flask", "3.1.3"),
        ("waitress", "waitress", "3.0.2"),
    ):
        package = site_packages / module_name
        metadata = site_packages / f"{distribution}-{version}.dist-info"
        package.mkdir()
        metadata.mkdir()
        (package / "__init__.py").write_text("# fixture\n", encoding="utf-8")
        (metadata / "METADATA").write_text(
            f"Name: {distribution}\nVersion: {version}\n",
            encoding="utf-8",
        )
    supply_chain = runtime / "supply-chain"
    supply_chain.mkdir()
    lock = b"Flask==3.1.3 --hash=sha256:" + b"1" * 64 + b"\nwaitress==3.0.2 --hash=sha256:" + b"2" * 64 + b"\n"
    sbom = (
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [
                    {
                        "type": "framework",
                        "name": "Python",
                        "version": "3.13.7",
                        "hashes": [{"alg": "SHA-256", "content": "a" * 64}],
                    },
                    {
                        "type": "library",
                        "name": "Flask",
                        "version": "3.1.3",
                        "hashes": [{"alg": "SHA-256", "content": "1" * 64}],
                    },
                    {
                        "type": "library",
                        "name": "waitress",
                        "version": "3.0.2",
                        "hashes": [{"alg": "SHA-256", "content": "2" * 64}],
                    },
                ],
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    notices = b"Python license\nFlask BSD-3-Clause\nwaitress ZPL-2.1\n"
    (supply_chain / "requirements.lock").write_bytes(lock)
    (app / "requirements-win-amd64.lock").write_bytes(lock)
    (supply_chain / "sbom.cdx.json").write_bytes(sbom)
    (supply_chain / "THIRD-PARTY-NOTICES.txt").write_bytes(notices)
    provenance = {
        "schema": 1,
        "python": {
            "implementation": "CPython",
            "version": "3.13.7",
            "architecture": "amd64",
            "source_url": "https://www.python.org/ftp/python/3.13.7/python-3.13.7-embed-amd64.zip",
            "source_sha256": "a" * 64,
        },
        "components": {"Flask": "3.1.3", "waitress": "3.0.2"},
        "artifacts": {
            "requirements_lock": {
                "file": "supply-chain/requirements.lock",
                "sha256": hashlib.sha256(lock).hexdigest(),
            },
            "sbom": {
                "file": "supply-chain/sbom.cdx.json",
                "sha256": hashlib.sha256(sbom).hexdigest(),
            },
            "third_party_notices": {
                "file": "supply-chain/THIRD-PARTY-NOTICES.txt",
                "sha256": hashlib.sha256(notices).hexdigest(),
            },
        },
        "runtime": {},
    }
    (supply_chain / "runtime-provenance.json").write_text(
        json.dumps(provenance, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    refresh_runtime_mapping(runtime)
    return payload, runtime


def build_fixture_package(root: Path, name: str = "release") -> BuildResult:
    inputs = root / name
    inputs.mkdir()
    payload, runtime = create_inputs(inputs)
    output_dir = inputs / "out"
    output_dir.mkdir()
    return build_package(
        payload,
        runtime,
        output_dir / ARTIFACT_NAME,
        _test_fixture=True,
    )


def extract_tool(result: BuildResult, destination: Path) -> Path:
    destination.mkdir()
    with zipfile.ZipFile(result.artifact_path, "r") as archive:
        archive.extractall(destination)
    return destination / "_V2安装工具"


def load_fixture_bundle(root: Path, name: str = "release") -> tuple[BuildResult, Bundle]:
    result = build_fixture_package(root, name=name)
    tool = extract_tool(result, root / f"{name}-extracted")
    return result, Bundle.load(tool, _test_fixture=True)
