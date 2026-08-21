import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  { ignores: ['dist', 'public/fixtures', 'node_modules'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // Build tooling runs in Node, not the browser.
    files: ['scripts/**/*.mjs', '*.config.{js,ts}'],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      // Colour must come from tokens. tailwind.config.ts replaces the default palette and
      // scripts/check-tokens.mjs catches hand-written literals.
      'no-restricted-syntax': [
        'error',
        {
          selector: "Literal[value=/#(?:[0-9a-fA-F]{3,4}){1,2}$/]",
          message: 'No colour literals. Use a design token from src/design/tokens.css.',
        },
      ],
    },
  },
);
