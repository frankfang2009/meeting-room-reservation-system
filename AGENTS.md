# Repository working rules

- `v2/` is the active Meeting Room Reservation System V2.0.0 codebase.
- V2 is a fresh installation. Never read, migrate, modify, or delete V1 data.
- Preserve the roles `admin | employee`, the authenticated shared-calendar visibility rules,
  employee-owned history boundary, and the server-side public-display allowlist.
- Database generation, identity, and integrity failures must fail closed. Reservation records,
  occupied slots, and events must remain transactionally consistent.
- Production setup binds loopback first and opens the LAN listener only after committed setup.
- Do not add demo passwords, synthetic production records, client-generated business IDs, or
  private-to-public projection in the browser.
- Read `v2/docs/PRODUCT-CONTRACT.md`, `v2/frontend/DESIGN-CONTRACT.md`, and
  `v2/docs/RELEASE-CHECKLIST.md` before changing product behavior or release code.
- Use `v2/scripts/check.sh` for the repeatable local gate. Formal Windows acceptance remains a
  separate external requirement.
- Do not modify V1 directories while working on V2. Do not reset, clean, or overwrite unrelated
  working-tree changes.
