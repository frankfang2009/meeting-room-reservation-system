from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = Path(__file__).resolve().parents[1]
SKELETON_DIR = TOOL_DIR / "发布骨架"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ReleaseUxDocumentationTests(unittest.TestCase):
    def assert_contains_all(self, text: str, expected: tuple[str, ...]) -> None:
        for phrase in expected:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_start_scripts_never_promise_that_the_old_address_stays_valid(self):
        start_script = read_text(SKELETON_DIR / "① 启动系统.bat")
        autostart_script = read_text(
            SKELETON_DIR / "③ 设置开机自动启动.bat"
        )
        combined = start_script + "\n" + autostart_script

        self.assertNotIn("同事可以继续使用原来的局域网地址", combined)
        self.assert_contains_all(
            combined,
            (
                "管理员",
                "当前“同事访问”地址",
                "地址发生变化",
                "复制新地址发给同事",
            ),
        )
        self.assertIn("不能保证一直使用原地址", autostart_script)

    def test_user_guide_covers_one_click_upgrade_and_real_user_impact(self):
        guide = read_text(SKELETON_DIR / "使用说明.txt")

        self.assert_contains_all(
            guide,
            (
                "本地固定盘",
                "不要放共享盘、U 盘、移动硬盘或网盘同步目录",
                "先让正在使用的同事保存当前页面上的操作",
                "正在提交的预约可能需要在升级后刷新页面并重试",
                "不需要手工复制数据库、迁移数据或改配置",
                "有多套会议室预约系统",
                "升级器不会自行猜测",
                "账号/会议室/预约数据已保留",
                "已恢复运行还是保持停止",
                "地址未变、已变化或暂时无法确认",
                "复制新地址",
                "重新登录",
                "预约数据不会因此变化",
                "VPN",
                "DHCP",
                "防火墙",
                "UAC",
                "SmartScreen",
                "EDR",
                "AppLocker",
                "真实 Windows",
                "正在请求管理员授权",
                "meetingroom_upgrade_launcher.log",
            ),
        )

    def test_windows_checklist_requires_network_identity_and_recovery_cases(self):
        checklist = read_text(
            SKELETON_DIR
            / "_程序文件"
            / "给网管的首次验收清单模板.txt"
        )

        self.assert_contains_all(
            checklist,
            (
                "普通用户看不到地址管理提醒",
                "模拟 IP 或端口变化",
                "地址连续变化",
                "原账号、会议室和预约数据均不变",
                "升级前正在运行时",
                "升级前已停止时",
                "最终运行状态",
                "有两套有效安装时升级器不会猜目录",
                "8080 端口被另一套程序占用",
                "强杀或重启",
                "成功事务的完整数据快照已经安全清理",
                "杀毒软件/EDR",
                "AppLocker",
                "VPN",
                "DHCP",
                "真实同事电脑访问",
                "窗口均不会静默关闭",
                "meetingroom_upgrade_launcher.log",
            ),
        )

    def test_release_notes_do_not_hide_unsigned_bat_or_lan_boundaries(self):
        maintainer_guide = read_text(TOOL_DIR / "README-出包说明.txt")
        root_readme = read_text(PROJECT_ROOT / "README.md")
        changelog = read_text(PROJECT_ROOT / "CHANGELOG.md")
        candidate_note = read_text(
            PROJECT_ROOT / "02_开发工作区" / "V1.0.2-候选版说明.txt"
        )
        combined = "\n".join(
            (maintainer_guide, root_readme, changelog, candidate_note)
        )

        self.assert_contains_all(
            combined,
            (
                "版本提交前只",
                "回环",
                "多套",
                "安装身份",
                "短暂停机",
                "手工迁移",
                "重新登录",
                "成功事务",
                "失败或未完成事务",
                "UAC",
                "SmartScreen",
                "杀毒软件/EDR",
                "AppLocker",
                "防火墙",
                "VPN",
                "DHCP",
                "物理断电",
                "另一台同事电脑",
                "入口日志",
            ),
        )
        self.assertIn(
            "回环健康检查不能证明同事电脑一定可访问",
            candidate_note,
        )

    def test_shipped_skeleton_text_keeps_windows_line_endings(self):
        paths = (
            SKELETON_DIR / "① 启动系统.bat",
            SKELETON_DIR / "② 立即备份.bat",
            SKELETON_DIR / "③ 设置开机自动启动.bat",
            SKELETON_DIR / "④ 停止本次后台系统.bat",
            SKELETON_DIR / "⑤ 取消开机自动启动.bat",
            SKELETON_DIR / "使用说明.txt",
            SKELETON_DIR
            / "_程序文件"
            / "给网管的首次验收清单模板.txt",
            SKELETON_DIR / "_程序文件" / "版本与校验信息.txt",
        )
        for path in paths:
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                self.assertIn(b"\r\n", raw)
                self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()
