# V2.5 Documentation Refresh Implementation Record

> **Status:** Original V2.5 documentation batches completed locally on 2026-08-30;
> rebased onto V2.5.1 on 2026-08-31 for final reconciliation, verification, and release
> integration. This record is not a recurring task.

**Goal:** Align current V2.5.1 engineering, release, user, help-center, and security documentation with the current implemented product while retaining dated V2.5.0 evidence as clearly labeled history.

**Architecture:** Treat executable code, locked dependency files, current contracts, CI workflows, and GitHub Release metadata as the sources of truth. Update current-facing documents; add visible historical/superseded banners to completed task records instead of deleting or rewriting evidence. The dated V2.5.0 Windows guide remains historical evidence, not the V2.5.1 execution entry.

**Tech Stack:** Markdown, plain-text operator notes, YAML comments, Python CLI help strings, GitHub Actions, Node help-center validation, Python/Flask/Waitress fixed runtime.

**Spec:** User-approved documentation audit, `v2/docs/PRODUCT-CONTRACT.md`, `v2/docs/RELEASE-CHECKLIST.md`, and `v2/docs/V2.5.0-WINDOWS-PHYSICAL-ACCEPTANCE-GUIDE.md`.

---

## Global constraints

- Work only in the current linked V2 worktree on `codex/v25-doc-refresh`.
- Do not change product behavior, V1 files/data, `.mimosa/`, or `.zcode/`.
- Keep `formal_external_release_allowed=false` for Windows until all ordinary-user physical gates pass.
- Distinguish the historical official V2.5.0 macOS arm64 Release from the V2.5.1 release in progress and the internal-only Windows candidate.
- Preserve dated evidence and completed task documents; mark them historical or superseded rather than deleting them.
- The original 2026-08-30 no-push/no-release checkpoint was superseded by the user's
  2026-08-31 authorization to complete V2.5.1 release engineering without further
  approval pauses. This branch remains documentation-only.

### Task 1: Correct engineering, runtime, upgrade, and release truth

**Files:**

- Modify: `AGENTS.md`
- Modify: `.github/SECURITY.md`
- Modify: `.github/workflows/v2-windows-acceptance.yml`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `v2/README.md`
- Modify: `v2/backend/README.md`
- Modify: `v2/docs/API-CONTRACT.md`
- Modify: `v2/docs/ARCHITECTURE.md`
- Modify: `v2/docs/PRODUCT-CONTRACT.md`
- Modify: `v2/docs/RELEASE-CHECKLIST.md`
- Modify: `v2/docs/SECURITY-DEPLOYMENT.md`
- Modify: `v2/docs/T1-WINDOWS-AUTOMATION.md`
- Modify: `v2/installer/README.md`
- Modify documentation strings only: `v2/installer/assemble_payload.py`, `v2/installer/build_package.py`

- [x] Replace stale V2.1/V2.2 current-version labels with current V2/V2.5 wording without changing dated history.
- [x] Document Python 3.13.14, Flask 3.1.3, Waitress 3.0.2, Node 22.17.1, and supported OS boundaries.
- [x] Correct backup restore compatibility: restore sidecars accept schema 1 or current schema 4; startup migration can catch up schemas 1/2/3 to 4.
- [x] Record the official V2.5 macOS arm64 Release assets/checksums and keep Windows internal-only.
- [x] Make installer examples version-derived or V2.5-current and keep V2.1.0 as the only supported legacy update source.
- [x] Run focused grep checks and `git diff --check`.
- [x] Commit the batch.

### Task 2: Correct user-facing and help-center guidance

**Files:**

- Modify: `v2/docs/help/README.md`
- Modify: `v2/docs/help/ARTICLE-EVIDENCE.md`
- Modify: `v2/docs/help/content/data/dt-metrics.md`
- Modify: `v2/docs/help/content/reminders/rm-sound.md`
- Modify: `v2/docs/help/content/booking/bk-visibility.md`
- Reference without duplicating: `v2/docs/help/content/security/sec-who-sees.md`

- [x] Correct cancellation metrics from “rate” to displayed cancellation count.
- [x] Match reminder sound language and behavior to current UI/code.
- [x] Fix repository-root notice links.
- [x] Reduce duplicated permissions guidance by linking to the canonical security article.
- [x] Run `node v2/docs/help/test.mjs`, build the help artifact, and run `git diff --check`.
- [x] Commit the batch.

### Task 3: Historicalize obsolete prototype and completed task documents

**Files:**

- Modify: `v2/frontend/DESIGN-CONTRACT.md`
- Modify: `v2/docs/FEATURE-INTERACTION-REGRESSION-CHECKLIST.md`
- Modify: `README-先看这里.txt`
- Modify: `CHANGELOG.md`
- Modify: `CODE-REVIEW-FIX-LIST.md`
- Modify: `v2/docs/T2-WINDOWS-TASK.md`
- Modify: `v2/docs/V241-WINDOWS-ACCEPTANCE-TASK.md`

- [x] Add clear current-authority/superseded banners while retaining historical evidence.
- [x] Stop obsolete Sites/prototype scripts and missing paths from reading as current instructions.
- [x] Point current operators to V2.5 contracts, changelog, release checklist, and Windows physical guide.
- [x] Run focused stale-instruction searches and `git diff --check`.
- [x] Commit the batch.

### Task 4: Refresh workspace routing state

**Files:**

- Modify outside the feature branch: workspace-root `STATE.md` (local coordination file)

- [x] Update the workspace topology/current-version summary to V2.5 and the active documentation branch.
- [x] Preserve prior V2.4/V2.4.1 facts as history and do not modify root V1 git state.

### Task 5: Final verification and handoff

- [x] Run `V2_PYTHON=... ./v2/scripts/check.sh` outside the network sandbox because tests bind loopback ports.
- [x] Run targeted searches for stale active claims and inspect the full diff/stat/status.
- [x] Request final code/document review from a fresh subagent.
- [x] Report changed scope, verification evidence, remaining physical Windows gate, branch, and unpushed status.

### 2026-08-31 V2.5.1 reconciliation extension

- [x] Rebase `codex/v25-doc-refresh` onto the latest V2.5.1 `main`, including the
  release-preparation merge and Windows rollback-recovery fix.
- [x] Change current-version claims to V2.5.1 while preserving the 2026-08-29 V2.5.0
  release and Windows acceptance evidence as labeled history.
- [x] Refresh workspace-root `STATE.md` topology and current-version summary without
  changing the root V1 git index, branch, or other files.
- [x] Run the full `check.sh`, help-center tests, reproducible help build, targeted
  stale-claim searches, and `git diff --check`.
- [x] Obtain a fresh read-only subagent final review and resolve any validated findings.
- [x] Push the rebased documentation branch, merge its green PR #50, and delete the
  merged remote branch.
