#!/usr/bin/env node
/**
 * Copy the canonical API fixtures into public/ so Vite can serve them statically.
 *
 * fixtures/api/ at the repo root is the single source of truth; public/fixtures/ is a
 * build artifact and is gitignored. Running this on predev/prebuild removes any chance of
 * the two drifting apart.
 */

import { copyFileSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const source = new URL('../../fixtures/api', import.meta.url).pathname;
const target = new URL('../public/fixtures', import.meta.url).pathname;

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });

const files = readdirSync(source).filter((f) => f.endsWith('.json'));
for (const file of files) copyFileSync(join(source, file), join(target, file));

console.log(`synced ${files.length} fixtures -> public/fixtures`);
