#!/usr/bin/env python3
"""制作会议室预约系统的单文件累计升级包。

本工具只在维护者电脑上运行。它把一个经过严格校验的完整负载目录打成
确定性 ZIP，再与 BAT/PowerShell 模板拼接；成品写出前后都会被反向拆解校验。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import os
import re
import stat
import sys
import tempfile
import textwrap
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PS_MARKER = "__UPGRADE_PS1_BELOW__"
PAYLOAD_MARKER = "__UPGRADE_PAYLOAD_BELOW__"
VERSION_PLACEHOLDER = "__PACKAGE_VERSION__"
SHA256_PLACEHOLDER = "__PAYLOAD_SHA256__"

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_STUB = TOOL_DIR / "bat头部模板.bat"
DEFAULT_POWERSHELL = TOOL_DIR / "升级主逻辑.ps1"
DEFAULT_FROZEN_REQUIREMENTS = (
    TOOL_DIR.parent
    / "Windows部署目录-V1.0.0"
    / "_程序文件"
    / "requirements.txt"
)

TOP_LEVEL_FILES = {
    "① 启动系统.bat",
    "② 立即备份.bat",
    "③ 设置开机自动启动.bat",
    "④ 停止本次后台系统.bat",
    "⑤ 取消开机自动启动.bat",
    "使用说明.txt",
}
PROGRAM_FILES = {
    "_程序文件/app.py",
    "_程序文件/server.py",
    "_程序文件/backup.py",
    "_程序文件/migrate_check.py",
    "_程序文件/requirements.txt",
    "_程序文件/版本.txt",
}
REQUIRED_FILES = TOP_LEVEL_FILES | PROGRAM_FILES
MANAGED_TREE_PREFIXES = ("_程序文件/static/", "_程序文件/templates/")
BLACKLISTED_COMPONENTS = {
    "data",
    "backups",
    "logs",
    "runtime",
    "_升级回滚",
    "_升级状态.json",
    "_升级锁",
}
BLACKLISTED_COMPONENT_KEYS = {item.casefold() for item in BLACKLISTED_COMPONENTS}
RESERVED_WINDOWS_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    "conin$",
    "conout$",
    *("com%d" % number for number in range(1, 10)),
    *("lpt%d" % number for number in range(1, 10)),
    "com¹",
    "com²",
    "com³",
    "lpt¹",
    "lpt²",
    "lpt³",
}

VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
BASE64_LINE_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class PackageBuildError(ValueError):
    """负载或模板不符合升级包契约。"""


@dataclass(frozen=True)
class BuildResult:
    version: str
    output_path: Path
    file_paths: Tuple[str, ...]
    zip_sha256: str
    zip_size: int
    package_size: int


def validate_version(version: str) -> Tuple[int, int, int]:
    """校验三段式版本号，并返回可用于数值比较的整数三元组。"""

    match = VERSION_RE.fullmatch(version or "")
    if match is None:
        raise PackageBuildError(
            "版本号必须是 X.Y.Z（三段非负整数，不能有前导零）"
        )
    parts = tuple(int(value) for value in match.groups())
    # System.Version 使用 Int32 保存各段；提前拒绝 Windows 端无法解析的值。
    if any(value > 2_147_483_647 for value in parts):
        raise PackageBuildError("版本号的每一段都不能大于 2147483647")
    return parts  # type: ignore[return-value]


def _windows_path_key(relative_path: str) -> str:
    """近似 Windows 的大小写/Unicode 不敏感路径键，用于发现冲突。"""

    return unicodedata.normalize("NFC", relative_path).casefold()


def _register_windows_path(seen_paths: Dict[str, str], relative_path: str) -> None:
    """把路径登记到 Windows 视图；重复或大小写冲突时立即失败。"""

    windows_key = _windows_path_key(relative_path)
    previous = seen_paths.get(windows_key)
    if previous is not None:
        raise PackageBuildError(
            "负载路径在 Windows 下重复或仅大小写不同：%s / %s"
            % (previous, relative_path)
        )
    seen_paths[windows_key] = relative_path


def _assert_safe_relative_path(relative_path: str) -> Tuple[str, ...]:
    """校验将进入 ZIP 的 POSIX 相对路径，不做猜测性修复。"""

    if not relative_path or not isinstance(relative_path, str):
        raise PackageBuildError("负载中出现空路径")
    if relative_path.startswith(("/", "\\")):
        raise PackageBuildError("负载路径不能是绝对路径：%s" % relative_path)
    if "\\" in relative_path:
        raise PackageBuildError(
            "负载路径不能包含反斜杠（可能造成 Windows 路径穿越）：%s"
            % relative_path
        )
    if ":" in relative_path:
        raise PackageBuildError(
            "负载路径不能包含冒号、盘符或 NTFS ADS：%s" % relative_path
        )
    invalid_windows_character = next(
        (character for character in '<>"|?*' if character in relative_path), None
    )
    if invalid_windows_character is not None:
        raise PackageBuildError(
            "负载路径包含 Windows 非法字符 %s：%s"
            % (invalid_windows_character, relative_path)
        )
    if "\x00" in relative_path:
        raise PackageBuildError("负载路径不能包含 NUL 字符")

    parts = tuple(relative_path.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise PackageBuildError("负载路径包含空段、. 或 ..：%s" % relative_path)

    for part in parts:
        if any(ord(character) < 32 for character in part):
            raise PackageBuildError("负载路径包含控制字符：%s" % relative_path)
        utf16_code_units = len(part.encode("utf-16-le")) // 2
        if utf16_code_units > 255:
            raise PackageBuildError(
                "Windows 路径单个名称不能超过 255 个 UTF-16 代码单元：%s"
                % relative_path
            )
        if part.endswith((" ", ".")):
            raise PackageBuildError(
                "Windows 路径不能以空格或句点结尾：%s" % relative_path
            )
        windows_basename = part.split(".", 1)[0].casefold()
        if windows_basename in RESERVED_WINDOWS_BASENAMES:
            raise PackageBuildError(
                "负载路径使用了 Windows 保留名称：%s" % relative_path
            )
        if part.casefold() in BLACKLISTED_COMPONENT_KEYS:
            raise PackageBuildError(
                "负载路径包含禁止名称 %s：%s" % (part, relative_path)
            )
    return parts


def _is_allowed_directory(relative_path: str) -> bool:
    return (
        relative_path == "_程序文件"
        or relative_path == "_程序文件/static"
        or relative_path == "_程序文件/templates"
        or relative_path.startswith("_程序文件/static/")
        or relative_path.startswith("_程序文件/templates/")
    )


def _is_allowed_file(relative_path: str) -> bool:
    return relative_path in REQUIRED_FILES or relative_path.startswith(
        MANAGED_TREE_PREFIXES
    )


def _walk_payload_without_links(root: Path) -> Tuple[List[str], List[str]]:
    """不跟随链接地遍历负载，同时验证每一个目录项。"""

    if root.is_symlink():
        raise PackageBuildError("完整负载目录本身不能是符号链接：%s" % root)
    try:
        root_stat = root.stat()
    except OSError as exc:
        raise PackageBuildError("无法读取完整负载目录：%s" % root) from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise PackageBuildError("完整负载路径不是目录：%s" % root)

    files: List[str] = []
    directories: List[str] = []
    seen_windows_paths: Dict[str, str] = {}

    def walk(current: Path, prefix: str) -> None:
        try:
            entries = sorted(os.scandir(str(current)), key=lambda entry: entry.name)
        except OSError as exc:
            raise PackageBuildError("无法遍历负载目录：%s" % current) from exc

        for entry in entries:
            relative = "%s/%s" % (prefix, entry.name) if prefix else entry.name
            _assert_safe_relative_path(relative)
            _register_windows_path(seen_windows_paths, relative)

            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise PackageBuildError("无法读取负载项目：%s" % relative) from exc

            if stat.S_ISLNK(entry_stat.st_mode):
                raise PackageBuildError("负载中禁止符号链接：%s" % relative)
            if stat.S_ISDIR(entry_stat.st_mode):
                if not _is_allowed_directory(relative):
                    raise PackageBuildError("负载中出现白名单外目录：%s" % relative)
                directories.append(relative)
                walk(Path(entry.path), relative)
            elif stat.S_ISREG(entry_stat.st_mode):
                if not _is_allowed_file(relative):
                    raise PackageBuildError("负载中出现白名单外文件：%s" % relative)
                files.append(relative)
            else:
                raise PackageBuildError(
                    "负载中只允许普通文件和目录：%s" % relative
                )

    walk(root, "")
    return files, directories


def _read_utf8_without_bom(path: Path, description: str) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PackageBuildError("无法读取%s：%s" % (description, path)) from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise PackageBuildError("%s必须是 UTF-8 无 BOM：%s" % (description, path))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageBuildError("%s不是合法 UTF-8：%s" % (description, path)) from exc


def _canonical_requirements(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # 仅忽略“是否有最后一个换行”，其余空白、注释和约束必须与冻结清单一致。
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
    return normalized


def collect_payload(
    payload_dir: Path,
    version: str,
    frozen_requirements_path: Optional[Path] = None,
) -> Dict[str, bytes]:
    """校验完整累计负载，并返回稳定排序前的路径到内容快照。"""

    validate_version(version)
    root = Path(payload_dir)
    files, directories = _walk_payload_without_links(root)

    missing = sorted(REQUIRED_FILES - set(files))
    if missing:
        raise PackageBuildError("完整累计负载缺少文件：%s" % "、".join(missing))
    for required_directory in ("_程序文件/static", "_程序文件/templates"):
        if required_directory not in directories:
            raise PackageBuildError("完整累计负载缺少目录：%s" % required_directory)
    for prefix in MANAGED_TREE_PREFIXES:
        if not any(path.startswith(prefix) for path in files):
            raise PackageBuildError("%s 目录不能为空" % prefix.rstrip("/"))

    contents: Dict[str, bytes] = {}
    for relative in sorted(files):
        path = root.joinpath(*relative.split("/"))
        try:
            # 再检查一次，缩小校验后被替换为链接的竞态窗口。
            if path.is_symlink():
                raise PackageBuildError("负载中禁止符号链接：%s" % relative)
            contents[relative] = path.read_bytes()
        except OSError as exc:
            raise PackageBuildError("无法读取负载文件：%s" % relative) from exc

    version_path = "_程序文件/版本.txt"
    version_bytes = contents[version_path]
    if version_bytes.startswith(b"\xef\xbb\xbf"):
        raise PackageBuildError("Payload 版本.txt 必须是 UTF-8 无 BOM")
    try:
        version_text = version_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageBuildError("Payload 版本.txt 不是合法 UTF-8") from exc
    version_match = re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\r?\n)?",
        version_text,
    )
    if version_match is None:
        raise PackageBuildError(
            "Payload 版本.txt 必须只包含严格 X.Y.Z，可有一个末尾换行"
        )
    payload_version = ".".join(version_match.groups())
    validate_version(payload_version)
    if payload_version != version:
        raise PackageBuildError(
            "Payload 版本.txt（%s）与出包参数（%s）不一致"
            % (payload_version, version)
        )

    frozen_path = Path(frozen_requirements_path or DEFAULT_FROZEN_REQUIREMENTS)
    frozen_text = _read_utf8_without_bom(frozen_path, "V1.0.0 冻结依赖清单")
    requirements_bytes = contents["_程序文件/requirements.txt"]
    if requirements_bytes.startswith(b"\xef\xbb\xbf"):
        raise PackageBuildError("Payload requirements.txt 必须是 UTF-8 无 BOM")
    try:
        requirements_text = requirements_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageBuildError("Payload requirements.txt 不是合法 UTF-8") from exc
    if _canonical_requirements(requirements_text) != _canonical_requirements(frozen_text):
        raise PackageBuildError(
            "Payload requirements.txt 与 V1.0.0 冻结依赖清单不一致；本期不能升级 runtime 依赖"
        )

    return contents


def build_deterministic_zip(files: Mapping[str, bytes]) -> bytes:
    """将已校验的文件快照打成元数据固定、顺序固定的 ZIP。"""

    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for relative in sorted(files):
            _assert_safe_relative_path(relative)
            info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.internal_attr = 0
            info.extra = b""
            archive.writestr(
                info,
                files[relative],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return output.getvalue()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _ensure_final_lf(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _replace_unique(text: str, placeholder: str, value: str) -> str:
    count = text.count(placeholder)
    if count != 1:
        raise PackageBuildError(
            "PowerShell 占位符 %s 必须恰好出现一次，实际为 %d 次"
            % (placeholder, count)
        )
    return text.replace(placeholder, value, 1)


def render_package(
    stub_text: str,
    powershell_template: str,
    version: str,
    zip_bytes: bytes,
) -> Tuple[bytes, str, str, str]:
    """渲染最终 BAT，返回成品、ZIP 哈希和规范化后的 stub/PS1。"""

    validate_version(version)
    zip_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    stub = _ensure_final_lf(_normalize_newlines(stub_text))
    stub_commands = [line.strip() for line in stub.splitlines() if line.strip()]
    if not stub_commands or re.fullmatch(
        r"(?i)exit\s+/b(?:\s+.+)?", stub_commands[-1]
    ) is None:
        raise PackageBuildError(
            "BAT 头部模板最后一条非空命令必须是 exit /b，防止继续执行嵌入内容"
        )
    powershell = _normalize_newlines(powershell_template)
    powershell = _replace_unique(powershell, VERSION_PLACEHOLDER, version)
    powershell = _replace_unique(powershell, SHA256_PLACEHOLDER, zip_sha256)
    powershell = _ensure_final_lf(powershell)

    encoded = base64.b64encode(zip_bytes).decode("ascii")
    encoded_lines = textwrap.wrap(encoded, width=76)
    if not encoded_lines:
        raise PackageBuildError("内部错误：ZIP Payload 为空")

    package_lf = (
        stub
        + PS_MARKER
        + "\n"
        + powershell
        + PAYLOAD_MARKER
        + "\n"
        + "\n".join(encoded_lines)
        + "\n"
    )
    package_bytes = package_lf.replace("\n", "\r\n").encode("utf-8")
    return package_bytes, zip_sha256, stub, powershell


def _marker_match(text_lf: str, marker: str) -> re.Match[str]:
    matches = list(re.finditer(r"(?m)^%s$" % re.escape(marker), text_lf))
    if len(matches) != 1:
        raise PackageBuildError(
            "成品中的整行标记 %s 必须恰好出现一次，实际为 %d 次"
            % (marker, len(matches))
        )
    return matches[0]


def _position_after_marker_line(text_lf: str, match: re.Match[str]) -> int:
    position = match.end()
    if position >= len(text_lf) or text_lf[position] != "\n":
        raise PackageBuildError("整行标记后必须有 CRLF 换行")
    return position + 1


def _verify_zip_contents(zip_bytes: bytes, expected_files: Mapping[str, bytes]) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes), mode="r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageBuildError("成品 Base64 不是有效 ZIP") from exc

    with archive:
        if archive.comment:
            raise PackageBuildError("ZIP 不应包含注释")
        actual_files: Dict[str, bytes] = {}
        seen_windows_paths: Dict[str, str] = {}
        for info in archive.infolist():
            relative = info.filename
            _assert_safe_relative_path(relative)
            if info.is_dir() or relative.endswith("/"):
                raise PackageBuildError("ZIP 中不应包含目录条目：%s" % relative)
            if not _is_allowed_file(relative):
                raise PackageBuildError("ZIP 中出现白名单外文件：%s" % relative)
            if relative in actual_files:
                raise PackageBuildError("ZIP 中出现重复路径：%s" % relative)
            windows_key = _windows_path_key(relative)
            previous = seen_windows_paths.get(windows_key)
            if previous is not None:
                raise PackageBuildError(
                    "ZIP 路径在 Windows 下重复或仅大小写不同：%s / %s"
                    % (previous, relative)
                )
            seen_windows_paths[windows_key] = relative
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                raise PackageBuildError("ZIP 中禁止符号链接：%s" % relative)
            if info.flag_bits & 0x1:
                raise PackageBuildError("ZIP 中禁止加密文件：%s" % relative)
            try:
                actual_files[relative] = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise PackageBuildError("无法读取 ZIP 文件：%s" % relative) from exc

        bad_file = archive.testzip()
        if bad_file is not None:
            raise PackageBuildError("ZIP CRC 校验失败：%s" % bad_file)

    actual_paths = set(actual_files)
    expected_paths = set(expected_files)
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise PackageBuildError(
            "ZIP 文件集合与源 Payload 不一致；缺少=%s，多出=%s"
            % (missing, extra)
        )
    for relative in sorted(expected_files):
        if actual_files[relative] != expected_files[relative]:
            raise PackageBuildError("ZIP 文件内容与源 Payload 不一致：%s" % relative)


def verify_package_bytes(
    package_bytes: bytes,
    expected_files: Mapping[str, bytes],
    expected_zip_sha256: str,
    expected_stub_lf: Optional[str] = None,
    expected_powershell_lf: Optional[str] = None,
) -> bytes:
    """反向拆分并验证成品，成功时返回解码后的原始 ZIP。"""

    if package_bytes.startswith(b"\xef\xbb\xbf"):
        raise PackageBuildError("最终 BAT 必须是 UTF-8 无 BOM")
    without_crlf = package_bytes.replace(b"\r\n", b"")
    if b"\n" in without_crlf or b"\r" in without_crlf:
        raise PackageBuildError("最终 BAT 必须全部使用 CRLF，不能混入 LF 或 CR")
    try:
        text_crlf = package_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageBuildError("最终 BAT 不是合法 UTF-8") from exc
    text_lf = text_crlf.replace("\r\n", "\n")

    ps_match = _marker_match(text_lf, PS_MARKER)
    payload_match = _marker_match(text_lf, PAYLOAD_MARKER)
    if ps_match.start() >= payload_match.start():
        raise PackageBuildError("PS1 标记必须位于 Payload 标记之前")

    ps_start = _position_after_marker_line(text_lf, ps_match)
    payload_start = _position_after_marker_line(text_lf, payload_match)
    stub = text_lf[: ps_match.start()]
    powershell = text_lf[ps_start : payload_match.start()]
    payload_text = text_lf[payload_start:]

    if expected_stub_lf is not None and stub != expected_stub_lf:
        raise PackageBuildError("成品中的 BAT stub 与模板不一致")
    if expected_powershell_lf is not None and powershell != expected_powershell_lf:
        raise PackageBuildError("stub 抽取出的 PowerShell 与替换后模板不一致")
    if re.search(r"(?m)^%s$" % re.escape(PAYLOAD_MARKER), powershell):
        raise PackageBuildError("抽取出的 PowerShell 错误包含独占整行 Payload 标记")
    if VERSION_PLACEHOLDER in powershell or SHA256_PLACEHOLDER in powershell:
        raise PackageBuildError("PowerShell 占位符未完全替换")

    if not payload_text.endswith("\n"):
        raise PackageBuildError("Base64 Payload 末尾必须有 CRLF")
    payload_lines = payload_text[:-1].split("\n")
    if not payload_lines or any(not line for line in payload_lines):
        raise PackageBuildError("Base64 Payload 包含空行")
    for index, line in enumerate(payload_lines):
        if index < len(payload_lines) - 1 and len(line) != 76:
            raise PackageBuildError("除最后一行外，Base64 每行必须为 76 字符")
        if len(line) > 76 or BASE64_LINE_RE.fullmatch(line) is None:
            raise PackageBuildError("Base64 Payload 含非法字符或行过长")
    try:
        zip_bytes = base64.b64decode("".join(payload_lines), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PackageBuildError("Base64 Payload 无法解码") from exc

    actual_sha256 = hashlib.sha256(zip_bytes).hexdigest()
    if actual_sha256 != expected_zip_sha256:
        raise PackageBuildError(
            "ZIP SHA-256 不一致：期望 %s，实际 %s"
            % (expected_zip_sha256, actual_sha256)
        )
    _verify_zip_contents(zip_bytes, expected_files)
    return zip_bytes


def _load_template(path: Path, description: str) -> str:
    text = _read_utf8_without_bom(path, description)
    if not text:
        raise PackageBuildError("%s不能为空：%s" % (description, path))
    if "\x00" in text:
        raise PackageBuildError("%s不能包含 NUL 字符：%s" % (description, path))
    return text


def _path_is_within(candidate: Path, directory: Path) -> bool:
    try:
        return os.path.commonpath((str(candidate), str(directory))) == str(directory)
    except ValueError:
        return False


def _same_existing_entry(first: Path, second: Path) -> bool:
    """按设备/文件号判断两个既存路径是否指向同一项，兼容大小写别名和硬链接。"""

    try:
        return os.path.samefile(str(first), str(second))
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False


def _existing_directory_is_within(candidate: Path, directory: Path) -> bool:
    """判断既存目录是否位于目标目录内，兼顾大小写别名与父级符号链接。"""

    candidate_resolved = candidate.resolve()
    directory_resolved = directory.resolve()
    if _path_is_within(candidate_resolved, directory_resolved):
        return True

    current = candidate
    while True:
        if _same_existing_entry(current, directory):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def build_package(
    payload_dir: Path,
    version: str,
    output_path: Optional[Path] = None,
    *,
    template_dir: Optional[Path] = None,
    frozen_requirements_path: Optional[Path] = None,
) -> BuildResult:
    """校验、构建、反向验证并原子交付一个升级 BAT。"""

    validate_version(version)
    payload_root = Path(payload_dir)
    templates = Path(template_dir or TOOL_DIR)
    stub_path = templates / "bat头部模板.bat"
    powershell_path = templates / "升级主逻辑.ps1"
    frozen_path = Path(frozen_requirements_path or DEFAULT_FROZEN_REQUIREMENTS)

    files = collect_payload(payload_root, version, frozen_path)
    zip_bytes = build_deterministic_zip(files)
    stub_text = _load_template(stub_path, "BAT 头部模板")
    powershell_text = _load_template(powershell_path, "PowerShell 主逻辑模板")
    package_bytes, zip_sha256, normalized_stub, normalized_powershell = render_package(
        stub_text, powershell_text, version, zip_bytes
    )
    # 写盘前先在内存中完整反向验证，避免用坏包覆盖已有成品。
    verify_package_bytes(
        package_bytes,
        files,
        zip_sha256,
        normalized_stub,
        normalized_powershell,
    )

    output = Path(output_path) if output_path is not None else Path.cwd() / (
        "升级到V%s.bat" % version
    )
    output = output.expanduser()
    if not output.parent.exists() or not output.parent.is_dir():
        raise PackageBuildError("输出目录不存在：%s" % output.parent)
    if output.exists() and output.is_dir():
        raise PackageBuildError("输出路径是目录，不能写入 BAT：%s" % output)

    payload_real = payload_root.resolve()
    output_real = output.resolve(strict=False)
    if _path_is_within(output_real, payload_real) or _existing_directory_is_within(
        output.parent, payload_root
    ):
        raise PackageBuildError("输出 BAT 不能写入完整负载目录内部：%s" % output)
    protected_paths = {
        Path(__file__).resolve(),
        stub_path.resolve(),
        powershell_path.resolve(),
        frozen_path.resolve(),
    }
    if output_real in protected_paths or any(
        _same_existing_entry(output, protected) for protected in protected_paths
    ):
        raise PackageBuildError("输出路径不能覆盖生成器、模板或冻结清单：%s" % output)
    if output.exists() and any(
        _same_existing_entry(
            output, payload_root.joinpath(*relative.split("/"))
        )
        for relative in files
    ):
        raise PackageBuildError("输出路径不能覆盖源 Payload 文件：%s" % output)

    temporary_path: Optional[Path] = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".%s." % output.name,
            suffix=".tmp",
            dir=str(output.parent),
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(package_bytes)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        # 按实施计划要求，重新读取真正写出的字节并再次反向验证。
        written_bytes = temporary_path.read_bytes()
        current_files = collect_payload(payload_root, version, frozen_path)
        if current_files != files:
            raise PackageBuildError("构建期间源 Payload 发生变化，请重新制作升级包")
        verify_package_bytes(
            written_bytes,
            current_files,
            zip_sha256,
            normalized_stub,
            normalized_powershell,
        )
        os.replace(str(temporary_path), str(output))
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    return BuildResult(
        version=version,
        output_path=output,
        file_paths=tuple(sorted(files)),
        zip_sha256=zip_sha256,
        zip_size=len(zip_bytes),
        package_size=len(package_bytes),
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="制作会议室预约系统完整累计单文件升级包"
    )
    parser.add_argument("payload", type=Path, help="完整累计负载目录")
    parser.add_argument("version", help="目标版本号，例如 1.0.1")
    parser.add_argument("--out", type=Path, help="输出 BAT 路径")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        result = build_package(args.payload, args.version, args.out)
    except (PackageBuildError, OSError) as exc:
        print("制作失败：%s" % exc, file=sys.stderr)
        return 1

    print("升级包制作成功")
    print("版本：V%s" % result.version)
    print("文件数量：%d" % len(result.file_paths))
    print("文件清单：")
    for relative in result.file_paths:
        print("  - %s" % relative)
    print("ZIP SHA-256：%s" % result.zip_sha256)
    print("ZIP 大小：%d 字节" % result.zip_size)
    print("最终大小：%d 字节" % result.package_size)
    print("输出：%s" % result.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
