# V2.5 Documentation Refresh Implementation Record

> **Status:** Completed locally on 2026-08-30 after independent final re-review. This
> record documents the approved scope,
> implementation batches, review checkpoints, and verification; it is not a recurring task.

**Goal:** Align current V2.5 engineering, release, user, help-center, and security documentation with the shipped product while retaining dated evidence as clearly labeled history.

**Architecture:** Treat executable code, locked dependency files, current contracts, CI workflows, GitHub Release metadata, and the V2.5 Windows acceptance guide as the sources of truth. Update current-facing documents; add visible historical/superseded banners to completed task records instead of deleting or rewriting evidence.

**Tech Stack:** Markdown, plain-text operator notes, YAML comments, Python CLI help strings, GitHub Actions, Node help-center validation, Python/Flask/Waitress fixed runtime.

**Spec:** User-approved documentation audit, `v2/docs/PRODUCT-CONTRACT.md`, `v2/docs/RELEASE-CHECKLIST.md`, and `v2/docs/V2.5.0-WINDOWS-PHYSICAL-ACCEPTANCE-GUIDE.md`.

---

## Global constraints

- Work only in the current linked V2 worktree on `codex/v25-doc-refresh`.
- Do not change product behavior, V1 files/data, `.mimosa/`, `.zcode/`, or GitHub release state.
- Keep `formal_external_release_allowed=false` for Windows until all ordinary-user physical gates pass.
- Distinguish the official V2.5 macOS arm64 Release from the internal Windows candidate.
- Preserve dated evidence and completed task documents; mark them historical or superseded rather than deleting them.
- Commit each completed batch locally. Do not push, merge, tag, publish, or create a release.

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
