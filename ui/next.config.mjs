import env from '@next/env';
import path from 'path';

env.loadEnvConfig(path.resolve(process.cwd(), '..'), undefined, undefined, true);

// Serve the UI under a path (`VISTA_BASE_PATH=/vista/ui`), as the hosted
// deployment does behind its gateway. Fixed at build time. Next prefixes pages,
// assets and <Link>; client `fetch`/`EventSource` go through `lib/api-url.ts`,
// which reads the same value from NEXT_PUBLIC_BASE_PATH below. Not the
// deployment flag, which the backend reports at runtime.
const basePath = (process.env.VISTA_BASE_PATH ?? '').replace(/\/+$/, '');

/** @type {import('next').NextConfig} */
const nextConfig = {
  ...(basePath ? { basePath } : {}),
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
  reactStrictMode: true,
  // VISTA Dev.app already exposes Chromium DevTools through its View menu.
  // Keep Next.js development behaviour without its floating on-screen button.
  devIndicators: false,
  serverExternalPackages: ["@modelcontextprotocol/sdk"],
  // Emit `.next/standalone` -- a server plus only the traced `node_modules`
  // files, so the prebuilt package ships a runnable UI without the 493 MB
  // install. `npm run dev` and `next start` are unaffected; this only adds an
  // output directory.
  //
  // Two consequences the packaging build has to handle: `public/` and
  // `.next/static/` are NOT copied into it (they are assumed to be on a CDN),
  // and the runtime no longer runs the `loadEnvConfig` call above, since that
  // happens when this config is read at build time. The two server-side env
  // vars in `app/api/_backend.ts` therefore have to come from the launcher's
  // environment, which is what `scripts/launch.sh` already exports.
  output: 'standalone',
};

export default nextConfig;
