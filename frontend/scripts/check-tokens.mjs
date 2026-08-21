#!/usr/bin/env node
/**
 * Design-token guard.
 *
 * Two rules, enforced mechanically because they are exactly the rules that erode:
 *   1. No colour literal outside src/design/tokens.css.
 *   2. No purple, violet, indigo or fuchsia anywhere in src/.
 *
 * Tailwind's default palette contains those hues, so `theme.colors` is replaced rather than
 * extended in tailwind.config.ts. This script catches the other route in: a hand-written hex.
 *
 * Run via `npm run tokens:check`. Wire into CI before Stage 2.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = new URL('../src', import.meta.url).pathname;
const TOKEN_FILE = 'design/tokens.css';

const HEX = /#(?:[0-9a-fA-F]{3,4}){1,2}\b/g;
const RGB = /\brgba?\(\s*\d+\s*,/g;
const BANNED_HUE = /\b(purple|violet|indigo|fuchsia)\b/gi;
// Decorative effects that belong to landing pages, not an operations console.
const BANNED_EFFECT = /\b(bg-gradient-to-|drop-shadow-|backdrop-blur|animate-pulse)\S*/g;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(ts|tsx|css)$/.test(entry)) out.push(full);
  }
  return out;
}

/**
 * Blank out comments while preserving line numbers and offsets.
 *
 * Comments legitimately name the banned hues — tokens.css documents the prohibition, and
 * this script's own header lists them. Stripping comments is better than exempting files,
 * because a real `--accent: violet` in tokens.css would still be caught.
 */
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, p1) => p1 + ' '.repeat(m.length - p1.length));
}

const violations = [];

for (const file of walk(SRC)) {
  const rel = relative(SRC, file);
  const raw = readFileSync(file, 'utf8');
  const text = stripComments(raw);
  const isTokenFile = rel === TOKEN_FILE;

  const check = (pattern, label, skipTokenFile) => {
    if (skipTokenFile && isTokenFile) return;
    for (const match of text.matchAll(pattern)) {
      const line = text.slice(0, match.index).split('\n').length;
      violations.push(`${rel}:${line}  ${label}: ${match[0]}`);
    }
  };

  check(HEX, 'colour literal (use a token)', true);
  check(RGB, 'colour literal (use a token)', true);
  check(BANNED_HUE, 'banned hue', false);
  check(BANNED_EFFECT, 'banned decorative effect', false);
}

if (violations.length > 0) {
  console.error('Design token violations:\n');
  for (const violation of violations) console.error(`  ${violation}`);
  console.error(
    `\n${violations.length} violation(s). Colour lives in src/design/tokens.css only.`,
  );
  process.exit(1);
}

console.log('OK: no colour literals outside tokens.css, no banned hues or effects');
