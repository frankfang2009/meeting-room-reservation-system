from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Tuple

from v2.installer.build_package import ARTIFACT_NAME, BuildResult, build_package
from v2.installer.installer_core import Bundle


def create_inputs(root: Path) -> Tuple[Path, Path]:
    payload = root / "payload"
    program = payload / "_程序文件"
    (program / "app").mkdir(parents=True)
    (program / "static" / "assets").mkdir(parents=True)
    (program / "service.py").write_text(
        "# synthetic service fixture\n",
        encoding="utf-8",
    )
    (program / "app" / "__init__.py").write_text("# fixture\n", encoding="utf-8")
    (program / "static" / "index.html").write_text(
        "<!doctype html><title>V2 fixture</title>\n",
        encoding="utf-8",
    )
    (program / "static" / "assets" / "app.js").write_text(
        "console.log('fixture');\n",
        encoding="utf-8",
    )
    (payload / "使用说明.txt").write_text("fixture payload\n", encoding="utf-8")

    runtime = root / "runtime"
    (runtime / "Lib" / "site-packages").mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"synthetic-python-exe")
    (runtime / "pythonw.exe").write_bytes(b"synthetic-pythonw-exe")
    (runtime / "python313.zip").write_bytes(b"synthetic-stdlib")
    (runtime / "Lib" / "site-packages" / "fixture.txt").write_text(
        "fixture runtime\n",
        encoding="utf-8",
    )
    return payload, runtime


def build_fixture_package(root: Path, name: str = "release") -> BuildResult:
    inputs = root / name
    inputs.mkdir()
    payload, runtime = create_inputs(inputs)
    output_dir = inputs / "out"
    output_dir.mkdir()
    return build_package(payload, runtime, output_dir / ARTIFACT_NAME)


def extract_tool(result: BuildResult, destination: Path) -> Path:
    destination.mkdir()
    with zipfile.ZipFile(result.artifact_path, "r") as archive:
        archive.extractall(destination)
    return destination / "_V2安装工具"


def load_fixture_bundle(root: Path, name: str = "release") -> tuple[BuildResult, Bundle]:
    result = build_fixture_package(root, name=name)
    tool = extract_tool(result, root / f"{name}-extracted")
    return result, Bundle.load(tool)
