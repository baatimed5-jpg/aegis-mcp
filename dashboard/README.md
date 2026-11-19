# AegisMCP dashboard

Angular 22 admin UI for the AegisMCP gateway (standalone components, signals, Angular Router,
plain SCSS, hand-made SVG charts, no UI kit). Screens: Overview (KPIs, live audit feed over
WebSocket, status breakdown), Security events, Approvals queue, Policies + pipeline, Experiments.

## Requirements

- Node 24 + npm 11 (no global Angular CLI needed, `npx ng` uses the local one)
- The gateway running on `http://localhost:8000` with CORS allowed for `http://localhost:4200`

## Develop

```bash
cd dashboard
npm ci            # exact versions from package-lock.json
npm start         # ng serve on http://localhost:4200, API = http://localhost:8000
```

Open http://localhost:4200 and paste the gateway `ADMIN_TOKEN` on the sign-in screen.
The token is kept in `sessionStorage` for that tab only; it is never written into the code.

Alternative without CORS: `npm run start:proxy` serves on :4200 and proxies `/api` (REST and
WebSocket) to `http://localhost:8000` using `proxy.conf.json`.

## Build and test

```bash
npm run build     # production build -> dist/dashboard/browser (API calls use relative /api)
npm test          # unit tests (Vitest + jsdom, headless, single run)
```

## API base URL

| Build                         | File                                      | `apiBase`               |
|-------------------------------|-------------------------------------------|-------------------------|
| production / docker (default) | `src/environments/environment.ts`         | `''` (same-origin `/api`) |
| `npm start` (development)     | `src/environments/environment.development.ts` | `http://localhost:8000` |
| `npm run start:proxy`         | `src/environments/environment.proxy.ts`   | `''` (dev-server proxy) |

The WebSocket URL is derived from it: `ws(s)://<host>/api/ws/audit?token=...` (wss on https pages).

## Docker

```bash
docker build -t aegis-dashboard ./dashboard
```

Multi-stage image: Node builds the app, `nginx:1.27-alpine` serves it on port 80 and proxies
`/api/` (with WebSocket upgrade for `/api/ws/`) to `http://gateway:8000` (see `nginx.conf`).
Docker Compose maps it to host port 4200.

## Layout

```
src/app/
  core/       models, ApiService, AuthService (+ guard, interceptor), AuditStreamService (WebSocket + backoff)
  layout/     shell (sidebar + router outlet)
  pages/      login, overview, security, approvals, policies, experiments
  shared/     kpi-card, status-badge, score-bar, donut-chart (SVG)
src/environments/   API base per build configuration
```
