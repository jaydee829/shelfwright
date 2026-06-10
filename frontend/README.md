# Librarian frontend (Lift 2 Stage 2)

Vite + React + TypeScript SPA over the conversational Librarian.

## Develop
1. Copy `.env.example` to `.env.local` and fill in the Firebase web config.
2. Run the backend API on `localhost:8080` (uvicorn).
3. `npm install` then `npm run dev` — Vite proxies API paths to the backend.

## Test
- `npm run test` — Vitest + React Testing Library (backend mocked).
- `npm run lint`, `npm run build` (typecheck).

Production serving (FastAPI static + multi-stage Docker) and Playwright e2e are Stage 4.
