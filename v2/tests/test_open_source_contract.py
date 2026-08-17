from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"


class OpenSourceRepositoryContractTests(unittest.TestCase):
    def test_community_health_and_license_files_exist(self) -> None:
        required = (
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "README.md",
            "README.en.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SUPPORT.md",
            ".github/SECURITY.md",
            ".github/CODEOWNERS",
            ".github/ISSUE_TEMPLATE/bug.yml",
            ".github/ISSUE_TEMPLATE/feature.yml",
            ".github/pull_request_template.md",
            ".github/dependabot.yml",
            ".github/workflows/codeql.yml",
        )
        for relative in required:
            self.assertTrue((ROOT / relative).is_file(), relative)

        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        package = json.loads((ROOT / "v2/frontend/package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["license"], "Apache-2.0")

    def test_public_tree_has_no_private_source_paths_or_retired_brand_copy(self) -> None:
        tracked = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
        ).decode("utf-8").split("\0")
        forbidden = (
            "/Users/" + "frank",
            "/private/" + "tmp",
            "/var/" + "folders/",
            "Anth" + "ropic-like",
            "Cla" + "ude-like",
            "cla" + "ude-doorway",
        )
        for relative in tracked:
            if relative in (
                "",
                "CUSTOMIZATION-PLAN.md",
                "v2/tests/test_open_source_contract.py",
            ):
                continue
            path = ROOT / relative
            if not path.is_file():
                continue
            content = path.read_bytes().decode("utf-8", errors="ignore")
            for marker in forbidden:
                self.assertNotIn(marker, content, f"{relative}: {marker}")

    def test_current_login_asset_is_project_owned_and_neutral(self) -> None:
        app = (ROOT / "v2/frontend/src/App.jsx").read_text(encoding="utf-8")
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        asset = ROOT / "v2/frontend/public/assets/login/schedule-portal.svg"
        self.assertTrue(asset.is_file())
        self.assertIn("SPDX-License-Identifier: Apache-2.0", asset.read_text(encoding="utf-8"))
        self.assertIn("/assets/login/schedule-portal.svg", app)
        self.assertIn("schedule-portal.svg", notice)
        self.assertFalse((asset.parent / "cla" "ude-doorway-time.png").exists())

    def test_every_external_action_is_pinned_to_a_commit(self) -> None:
        action_pattern = re.compile(r"^\s*uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
        for workflow_path in sorted(WORKFLOW_ROOT.glob("*.yml")):
            workflow = workflow_path.read_text(encoding="utf-8")
            for action, revision in action_pattern.findall(workflow):
                if action.startswith("./"):
                    continue
                self.assertRegex(
                    revision,
                    r"^[0-9a-f]{40}$",
                    f"{workflow_path.name}: {action}@{revision}",
                )

    def test_codeql_waits_until_repository_is_public(self) -> None:
        workflow = (WORKFLOW_ROOT / "codeql.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("if: github.event.repository.private == false", workflow)
        self.assertIn("security-events: write", workflow)

    def test_normal_ci_never_builds_or_uploads_a_candidate(self) -> None:
        workflow = (WORKFLOW_ROOT / "ci.yml").read_text(encoding="utf-8")
        for job in ("repository-policy", "v2-linux", "v2-windows", "v2-macos"):
            self.assertIn(f"  {job}:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn('git diff --check "$PR_BASE_SHA...$PR_HEAD_SHA"', workflow)
        self.assertIn('git diff --check "$PUSH_BEFORE_SHA..$PUSH_HEAD_SHA"', workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertNotIn("v2-reproducible-build.sh", workflow)
        self.assertNotIn("build_package", workflow)

    def test_candidate_and_legacy_workflows_are_separated(self) -> None:
        candidate = (WORKFLOW_ROOT / "release-candidate.yml").read_text(encoding="utf-8")
        legacy = (WORKFLOW_ROOT / "windows-upgrade.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", candidate)
        self.assertIn('      - "v2.*"', candidate)
        self.assertNotIn("pull_request:", candidate)
        self.assertIn("v2/VERSION", candidate)
        self.assertIn("retention-days: 7", candidate)
        self.assertNotIn("contents: write", candidate)
        self.assertIn('      - "02_开发工作区/**"', legacy)
        self.assertIn("workflow_dispatch:", legacy)

    def test_source_publication_does_not_enable_formal_binary_release(self) -> None:
        builder = (ROOT / "v2/installer/build_package.py").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / ".github/SECURITY.md").read_text(encoding="utf-8")
        self.assertIn('"formal_external_release_allowed": False', builder)
        self.assertIn("不是正式 Windows 安装包", readme)
        self.assertIn("非正式发布", security)


if __name__ == "__main__":
    unittest.main()
