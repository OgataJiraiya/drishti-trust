# Shared Backend

This directory is reserved for the integrated FastAPI/security backend and common service layer.

Person 3 currently owns the provenance/security backend implementation. Shared backend changes that affect other modules should be called out clearly in pull requests.

Integration boundary:

- `POST /api/evidence` accepts Finding Schema v1
- shared summary/dashboard APIs consume persisted findings
- security/provenance services remain offline-capable

Do not place module-specific CV algorithms here unless they are genuinely shared infrastructure.
