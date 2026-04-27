# Frontend

Next.js 16 (App Router, Tailwind v4, shadcn/ui, i18next-stub) for the RAG SaaS UI. Currently a v0 placeholder; chat and document UIs land in V1.5.

## Local dev

```bash
npm install
npm run dev
```

Then open http://localhost:3000.

This project uses **npm**, not pnpm/yarn/bun — match the rest of the toolchain (CI, Dockerfile, root scripts).

## Stack notes

- **Next.js 16** — see [`AGENTS.md`](./AGENTS.md). The version has breaking changes from training-cached defaults; consult `node_modules/next/dist/docs/` for API specifics.
- **Tailwind v4** — config lives in `postcss.config.mjs` and the CSS file, not a separate `tailwind.config.ts`.
- **shadcn/ui** — pre-wired in `components.json`. Add components with `npx shadcn@latest add <name>`.
- **i18next** — the stub at `src/lib/i18n/` pins the locale set (`fr`, `ar`, `en`) and RTL list. Real locale routing is V1.5.

## Production build

```bash
npm run build
npm run start
```

The Docker image uses Next's standalone output (`output: "standalone"` in `next.config.ts`) and runs `node server.js`.

## Backend wiring

API base URL is read from `NEXT_PUBLIC_API_BASE` (defaults to `http://localhost:8000`).
