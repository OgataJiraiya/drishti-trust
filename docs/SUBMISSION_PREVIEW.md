# Limited public SIH submission preview

The `submission-preview` branch builds a static, browser-local demonstration of the core integrity assurance workflow. It uses the existing frontend demo fixtures. The displayed assessment, findings, and scores are demo data, not organization evidence or a complete DRISHTI platform deployment.

## Render configuration

Create a **Static Site** with these settings:

| Setting | Value |
| --- | --- |
| Branch | `submission-preview` |
| Root directory | `frontend` |
| Build command | `npm ci && npm run build` |
| Publish directory | `dist` |

Set these environment variables on the Static Site:

| Variable | Value |
| --- | --- |
| `VITE_DRISHTI_DEMO_MODE` | `true` |
| `VITE_DRISHTI_DEMO_SCENARIO` | `critical` |
| `VITE_DRISHTI_SUBMISSION_PREVIEW` | `true` |

In Render **Redirects/Rewrites**, add the SPA fallback:

| Source | Destination | Action |
| --- | --- | --- |
| `/*` | `/index.html` | `Rewrite` |

This serves `index.html` for direct visits and refreshes on `/findings`, `/distribution`, and `/reports`. Do not create a Web Service or deploy FastAPI for this preview. No API URL, bearer token, signing key, database, or intake capability is needed. The site contains only built frontend files.

The Render rewrite handles serving index.html for deep links. The application itself
preserves/restores demo scenario context: all four public routes are always mounted.
The previous application-level conditional route table, not the Render rewrite,
caused unselected sessions to redirect public detail pages to Overview.

## Assessment context and navigation

- A fresh `/` shows the AI Assurance Dossier and its assessment index.
- Findings, Distribution Shift, and Reports open immediately with the `critical`
  fixture when no explicit choice exists, including pasted URLs and refreshes.
- Opening an assessment or changing the compact selector stores only `critical`
  or `clean` in sessionStorage under `drishti-preview-scenario`. Other values are
  ignored. Explicit choices survive refreshes within the browser tab/session.
- The compact selector updates the current page without returning to Overview.
- **Change assessment** and **Reset demo** remove that key and return to the landing
  without reloading the site. If storage is blocked, in-memory selection still works.
- A default high-risk context does not write a preference; without an explicit
  selection, returning to `/` shows the assessment index.

The index previews factual high-risk fixture values in a narrow assurance snapshot.
The reference option uses the existing `clean` fixture unchanged, including its
inherited findings and explanatory text. No live analysis is represented or run.

## Scope

Navigation contains Overview, Findings, Distribution Shift, and Reports. The preview redirects other routes to Overview. It does not provide assessment creation, evidence intake, model upload, registration, revocation, or audit mutation. The normal application retains its full navigation when the preview flag is absent.

The critical fixture illustrates the score, coverage, system disposition, module statuses, authenticated evidence framing, findings, distribution shift, audit integrity, and report. Scores and statuses come from the existing fixtures. `UNKNOWN` is not safe; no findings and full coverage do not prove safety; a valid signature proves provenance rather than model safety; confidence is not attack probability.

## Local verification

```sh
cd frontend
VITE_DRISHTI_DEMO_MODE=true VITE_DRISHTI_DEMO_SCENARIO=critical VITE_DRISHTI_SUBMISSION_PREVIEW=true npm test -- --run
VITE_DRISHTI_DEMO_MODE=true VITE_DRISHTI_DEMO_SCENARIO=critical VITE_DRISHTI_SUBMISSION_PREVIEW=true npm run build
```

The preview does not call a backend. Build output should contain no intake UI or backend API client. Render serves the static files and their same-origin route fallback only.
