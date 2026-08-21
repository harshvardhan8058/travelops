#!/usr/bin/env node
/**
 * Copy the canonical API fixtures into public/ so Vite can serve them statically.
 *
 * fixtures/api/ at the repo root is the single source of truth; public/fixtures/ is a
 * build artifact and is gitignored. Running this on predev/prebuild removes any chance of
 * the two drifting apart.
 *
 * Runs as a `predev` and `prebuild` hook, so it must NEVER hard-fail: a crash here stops
 * the dev server from starting at all, which presents as "connection refused" on :5173 with
 * nothing obviously wrong. Warn loudly and continue instead.
 *
 * Inside Docker this resolves to /fixtures/api, which docker-compose.yml mounts read-only.
 */

import { copyFileSync, existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const source = new URL('../../fixtures/api', import.meta.url).pathname;
const target = new URL('../public/fixtures', import.meta.url).pathname;

if (!existsSync(source)) {
  console.warn(
    [
      '',
      `  WARNING: fixture source not found at ${source}`,
      '',
      '  The UI will still start, but any screen reading fixtures will show its error state.',
      '',
      '  In Docker: confirm docker-compose.yml mounts "./fixtures:/fixtures:ro" for the web',
      '  service. Locally: run this from the frontend/ directory of a full checkout.',
      '',
    ].join('\n'),
  );
  process.exit(0);
}

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });

const files = readdirSync(source).filter((f) => f.endsWith('.json'));
for (const file of files) copyFileSync(join(source, file), join(target, file));

console.log(`synced ${files.length} fixtures -> public/fixtures`);
