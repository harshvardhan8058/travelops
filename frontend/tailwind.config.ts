import type { Config } from 'tailwindcss';

/**
 * Tailwind is bound to the CSS variables in src/design/tokens.css.
 *
 * Every colour here resolves to a token. Nothing in a component may use a raw hex value
 * or an off-palette Tailwind colour, which is what keeps purple out permanently.
 *
 * Owner: Stream E.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    // `colors` is REPLACED rather than extended. Tailwind's default palette includes
    // purple, violet, indigo and fuchsia; leaving them available is how they get used.
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      inherit: 'inherit',

      base: 'var(--bg-base)',
      surface: 'var(--bg-surface)',
      raised: 'var(--bg-raised)',
      inset: 'var(--bg-inset)',

      border: {
        subtle: 'var(--border-subtle)',
        DEFAULT: 'var(--border-default)',
        strong: 'var(--border-strong)',
      },

      fg: {
        DEFAULT: 'var(--fg-primary)',
        secondary: 'var(--fg-secondary)',
        muted: 'var(--fg-muted)',
        inverse: 'var(--fg-inverse)',
      },

      accent: {
        DEFAULT: 'var(--accent)',
        hover: 'var(--accent-hover)',
        pressed: 'var(--accent-pressed)',
        subtle: 'var(--accent-subtle)',
        border: 'var(--accent-border)',
      },

      // Operational state ONLY. Never decorative.
      state: {
        ok: 'var(--state-ok)',
        warn: 'var(--state-warn)',
        crit: 'var(--state-crit)',
        info: 'var(--state-info)',
        neutral: 'var(--state-neutral)',
        'ok-bg': 'var(--state-ok-bg)',
        'warn-bg': 'var(--state-warn-bg)',
        'crit-bg': 'var(--state-crit-bg)',
        'info-bg': 'var(--state-info-bg)',
        'neutral-bg': 'var(--state-neutral-bg)',
      },
    },

    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        // Every operational number renders in this, with tabular figures.
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // 14px body: ops UI is denser than a marketing site.
        body: ['0.875rem', { lineHeight: '1.25rem' }],
        label: ['0.75rem', { lineHeight: '1rem', letterSpacing: '0.04em' }],
        caption: ['0.6875rem', { lineHeight: '0.875rem' }],
        'mono-sm': ['0.78125rem', { lineHeight: '1.125rem' }],
        subtitle: ['1rem', { lineHeight: '1.5rem' }],
        title: ['1.25rem', { lineHeight: '1.75rem' }],
        display: ['1.875rem', { lineHeight: '2.25rem', letterSpacing: '-0.011em' }],
      },
      borderRadius: {
        DEFAULT: 'var(--radius)',
        sm: 'var(--radius-sm)',
      },
      spacing: {
        rail: 'var(--rail-width)',
        topbar: 'var(--topbar-height)',
        timeline: 'var(--timeline-width)',
        row: 'var(--row-height)',
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
      },
      transitionDuration: {
        hover: 'var(--motion-hover)',
        panel: 'var(--motion-panel)',
        enter: 'var(--motion-enter)',
      },
      // No blur/glow utilities are added. Elevation is a 1px border, not a shadow.
      boxShadow: {
        none: 'none',
      },
    },
  },
  plugins: [],
} satisfies Config;
