# Agent Reliability Lab: dashboard

A React + TypeScript + Vite dashboard for the lab's FastAPI backend. It only displays
what the API returns; there is no sample or generated data.

Stack: Tailwind CSS with shadcn/ui components (re-themed in `src/index.css`), TanStack
Query for server data, React Router, and Recharts.

```bash
npm install
npm run dev        # http://localhost:5173, /api is proxied to http://localhost:8000
npm test           # Vitest + Testing Library, API mocked with MSW
npm run lint       # oxlint
npm run typecheck  # strict TypeScript
npm run build      # production build in dist/
```

Set `VITE_API_PROXY_TARGET` to proxy to a different backend in development, or
`VITE_API_BASE_URL` to point a production build straight at the API.

## Layout

- `src/api`: response types, the fetch client and one TanStack Query hook per endpoint
- `src/index.css`: design tokens; every colour comes from here
- `src/lib`: formatting, status → tone/label/icon mapping, chart colours
- `src/components/common`: page header, panel, KPI card, status badge, charts, query states
- `src/components/ui`: shadcn/ui components (generated, lightly restyled)
- `src/pages`: one file per page, with its tests next to it
- `src/test`: MSW server, render helper and fixture factories (tests only)
