"""Deterministic frontend dependency evidence for the V2 release artifact."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

try:
    from .installer_core import (
        InstallerError,
        assert_plain_file,
        json_bytes,
        sha256_bytes,
        tree_digest,
    )
except ImportError:
    from installer_core import (  # type: ignore
        InstallerError,
        assert_plain_file,
        json_bytes,
        sha256_bytes,
        tree_digest,
    )


FRONTEND_COMPONENTS_FILE = "_程序文件/app/frontend-production-components.json"


class FrontendSupplyChainError(InstallerError):
    """The npm lock or generated frontend evidence is incomplete."""


def _package_name(lock_path: str, item: Mapping[str, Any]) -> str:
    name = str(item.get("name", "")).strip()
    if name:
        return name
    marker = "node_modules/"
    if marker not in lock_path:
        raise FrontendSupplyChainError(f"无法从 package-lock 路径识别组件：{lock_path}")
    return lock_path.rsplit(marker, 1)[1]


def build_frontend_component_evidence(lock_file: Path) -> bytes:
    """Project package-lock v3 into the production-only shipped dependency set."""
    assert_plain_file(lock_file, "前端 package-lock.json")
    raw = lock_file.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") or len(raw) > 32 * 1024 * 1024:
        raise FrontendSupplyChainError("前端 package-lock.json 带 BOM 或体积异常")
    try:
        lock = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrontendSupplyChainError("前端 package-lock.json 不是有效 UTF-8 JSON") from error
    if lock.get("lockfileVersion") != 3 or not isinstance(lock.get("packages"), dict):
        raise FrontendSupplyChainError("前端依赖必须使用 package-lock v3")
    root = lock["packages"].get("")
    if not isinstance(root, dict) or not root.get("name") or not root.get("version"):
        raise FrontendSupplyChainError("前端 package-lock 缺少应用身份")

    components: list[Mapping[str, Any]] = []
    for lock_path, value in sorted(lock["packages"].items()):
        if not lock_path or not lock_path.startswith("node_modules/"):
            continue
        if not isinstance(value, dict) or value.get("dev") is True:
            continue
        name = _package_name(lock_path, value)
        version = str(value.get("version", "")).strip()
        integrity = str(value.get("integrity", "")).strip()
        license_name = str(value.get("license", "")).strip()
        resolved = str(value.get("resolved", "")).strip()
        if not version or not integrity.startswith("sha512-") or not license_name:
            raise FrontendSupplyChainError(f"前端生产依赖缺少版本、SHA-512 或许可证：{name}")
        if resolved and not resolved.startswith("https://"):
            raise FrontendSupplyChainError(f"前端生产依赖来源不是 HTTPS：{name}")
        components.append(
            {
                "name": name,
                "version": version,
                "integrity": integrity,
                "license": license_name,
                "resolved": resolved,
            }
        )
    if not components:
        raise FrontendSupplyChainError("前端 package-lock 未得到任何生产依赖")
    return json_bytes(
        {
            "schema": 1,
            "application": {"name": root["name"], "version": root["version"]},
            "packageLock": {
                "file": "package-lock.json",
                "sha256": sha256_bytes(raw),
            },
            "components": components,
        }
    )


def load_frontend_component_evidence(content: bytes) -> Mapping[str, Any]:
    try:
        evidence = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrontendSupplyChainError("payload 前端组件证据不是有效 JSON") from error
    components = evidence.get("components")
    package_lock = evidence.get("packageLock")
    if evidence.get("schema") != 1 or not isinstance(components, list) or not components:
        raise FrontendSupplyChainError("payload 前端组件证据结构无效")
    if not isinstance(package_lock, dict) or len(str(package_lock.get("sha256", ""))) != 64:
        raise FrontendSupplyChainError("payload 前端组件证据缺少 package-lock 摘要")
    seen: set[tuple[str, str]] = set()
    for component in components:
        if not isinstance(component, dict):
            raise FrontendSupplyChainError("payload 前端组件条目无效")
        key = (str(component.get("name", "")), str(component.get("version", "")))
        if not all(key) or key in seen:
            raise FrontendSupplyChainError("payload 前端组件身份缺失或重复")
        seen.add(key)
        integrity = str(component.get("integrity", ""))
        try:
            decoded = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
        except ValueError as error:
            raise FrontendSupplyChainError(f"前端组件 SHA-512 无效：{key[0]}") from error
        if not integrity.startswith("sha512-") or len(decoded) != 64 or not component.get("license"):
            raise FrontendSupplyChainError(f"前端组件完整性或许可证证据无效：{key[0]}")
    return evidence


def make_artifact_sbom(
    runtime_sbom: bytes,
    frontend_evidence: bytes,
    payload_records: Sequence[Mapping[str, Any]],
) -> bytes:
    try:
        runtime = json.loads(runtime_sbom)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrontendSupplyChainError("runtime SBOM 不是有效 JSON") from error
    if runtime.get("bomFormat") != "CycloneDX" or not isinstance(runtime.get("components"), list):
        raise FrontendSupplyChainError("runtime SBOM 结构无效")
    frontend = load_frontend_component_evidence(frontend_evidence)
    frontend_components = []
    for component in frontend["components"]:
        digest = base64.b64decode(component["integrity"].removeprefix("sha512-"), validate=True).hex()
        item: dict[str, Any] = {
            "type": "library",
            "group": "frontend",
            "name": component["name"],
            "version": component["version"],
            "purl": f"pkg:npm/{quote(component['name'], safe='/')}@{quote(component['version'])}",
            "hashes": [{"alg": "SHA-512", "content": digest}],
            "licenses": [{"license": {"name": component["license"]}}],
        }
        if component.get("resolved"):
            item["externalReferences"] = [{"type": "distribution", "url": component["resolved"]}]
        frontend_components.append(item)
    seed = hashlib.sha256(runtime_sbom + frontend_evidence).hexdigest()
    static_records = tuple(
        record
        for record in payload_records
        if str(record["path"]).startswith("_程序文件/app/static/")
    )
    return json_bytes(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed)),
            "version": 1,
            "metadata": {
                "component": {
                    "type": "application",
                    "name": "会议室预约系统 V2",
                    "version": "2.1.0",
                },
                "properties": [
                    {"name": "meeting-room-v2:frontend-package-lock-sha256", "value": frontend["packageLock"]["sha256"]},
                    {"name": "meeting-room-v2:frontend-dist-tree-sha256", "value": tree_digest(static_records)},
                    {"name": "meeting-room-v2:runtime-sbom-sha256", "value": sha256_bytes(runtime_sbom)},
                ],
            },
            "components": [*runtime["components"], *frontend_components],
        }
    )


def make_artifact_notices(runtime_notices: bytes, frontend_evidence: bytes) -> bytes:
    frontend = load_frontend_component_evidence(frontend_evidence)
    chunks = [runtime_notices.rstrip() + b"\n", b"\n===== Frontend production components =====\n"]
    for component in frontend["components"]:
        text = (
            f"\n--- {component['name']} {component['version']} ---\n"
            f"Registry: {component.get('resolved') or 'recorded in package-lock.json'}\n"
            f"Integrity: {component['integrity']}\n"
            f"License metadata: {component['license']}\n"
        )
        chunks.append(text.encode("utf-8"))
    return b"".join(chunks)
