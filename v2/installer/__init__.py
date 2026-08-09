"""会议室预约系统 V2 的全新安装与后续 V2 更新基线。"""

from .installer_core import (
    PRODUCT_GENERATION,
    RELEASE,
    SERVICE_PORT,
    VERSION,
    Bundle,
    InstallTransaction,
    InstallerError,
)

__all__ = [
    "PRODUCT_GENERATION",
    "RELEASE",
    "SERVICE_PORT",
    "VERSION",
    "Bundle",
    "InstallTransaction",
    "InstallerError",
]
