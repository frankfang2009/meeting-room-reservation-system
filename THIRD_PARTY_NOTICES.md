# Third-party notices

This source repository and its generated Windows candidate may include third-party software. Each dependency remains governed by its own license; Apache-2.0 does not replace those terms.

Important direct runtime dependencies include:

| Component | Use | License |
| --- | --- | --- |
| React / React DOM | Web interface | MIT |
| Phosphor Icons for React | Interface icons | MIT |
| Lucide Icons (SVG path data) | Offline help-center icons embedded in `v2/docs/help/build.mjs` | ISC |
| Flask | HTTP application framework | BSD-3-Clause |
| Waitress | WSGI server | ZPL-2.1 |
| CPython embeddable runtime | Windows runtime | Python-2.0 |

The authoritative dependency versions are recorded in `v2/frontend/package-lock.json`, `v2/backend/requirements-win-amd64.lock`, and `v2/backend/requirements.txt`. The candidate builder generates a CycloneDX SBOM and a complete `THIRD-PARTY-NOTICES.txt` sidecar from the locked frontend and Windows runtime inputs. Those generated files are the distribution-specific notice set and must accompany any approved candidate.

No third-party trademark is granted by this repository's license. Product names above identify compatible dependencies only.
