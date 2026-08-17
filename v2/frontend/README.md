# V2 React frontend

This directory contains the production React client for the V2.2.1 fresh-install
baseline. It keeps the frozen V2 DOM/class vocabulary and `styles.css`, but all
runtime state comes from the same-origin Flask API under `/api/v1`.

## Commands

```bash
npm test
npm run build
```

`npm run build` writes the static client to `dist/client`. Production machines
receive that prebuilt output and do not require Node.js.

## Security and data boundaries

- Authentication uses the HttpOnly Flask session cookie. Every write sends the
  CSRF token returned by `GET /api/v1/session`.
- Roles are exactly `admin | employee`. The UI mirrors permissions for clarity;
  Flask remains the authorization boundary.
- Reservation updates send `expectedRevision`, and retain the draft on slot or
  revision conflicts.
- `/display` only calls `/api/v1/display/today`. `public-contract.js` rejects
  any field outside the public allowlist instead of stripping private fields.
- No seed records, demo passwords, query-state routes, client-generated business
  IDs, or private-to-public projection are part of the production bundle.
