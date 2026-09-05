# DRISHTI analyst interface

U1 is NETRAVA's read-only, air-gap-ready assurance overview for DRISHTI. It presents the Person-3 backend's assessment context, assurance and coverage values, four module states, authenticated findings, audit-chain integrity, and explicit interpretation boundaries. It never calculates assurance or disposition.

## Stack and architecture

React, TypeScript, Vite, React Router, Lucide, Recharts-ready chart support, and a lightweight CSS-token component system. `src/api` owns typed contracts, request timeout/error normalization, and runtime response guards; `hooks/useDashboard.ts` coordinates reads; reusable shell/UI/assurance/finding components render the overview. No CDN, remote font, analytics, or cloud runtime call exists.

## Install and run

```sh
cd frontend
npm install
npm run dev
```

Vite proxies `/api` and `/health` to `http://127.0.0.1:8000` during development. For a production/backend origin, set `VITE_DRISHTI_API_URL`, for example:

```sh
VITE_DRISHTI_API_URL=http://127.0.0.1:8000 npm run build
```

The default base is relative (the documented Vite proxy in development, or same-origin when deployed). No cloud URL is embedded.

## Explicit demo mode

```sh
VITE_DRISHTI_DEMO_MODE=true VITE_DRISHTI_DEMO_SCENARIO=critical npm run dev
```

Scenarios: `complete-review`, `provisional`, `unknown`, `critical`, and `clean`. Demo mode always displays a `DEMO DATA` badge. Real-mode failures display OFFLINE and never silently substitute fixtures.

## Verification

```sh
npm test
npm run typecheck
npm run lint
npm run build
```

## Trust boundaries and scope

- Reads only `GET /health`, `/api/summary`, `/api/assessments`, and `/api/assessments/current`.
- Requests `trust_scope=authenticated`; untrusted exclusions remain visible.
- Backend score, status, coverage, and disposition are displayed—not derived.
- Signature validity is provenance authenticity, not detector correctness. Coverage is not safety. Confidence is evidence confidence, not attack probability.
- No admin credentials, bearer-token persistence, or browser private-key/signing workflow exists.

The workstation implements Overview, Findings Explorer, Distribution Shift, Assurance Graph, Reports, and System Status. Compatibility routes redirect to the closest functional workspace. All workspaces remain read-only: filtering and graph selection are local presentation state, report export uses the browser print dialog, and no analyst decision or lifecycle mutation is simulated.
