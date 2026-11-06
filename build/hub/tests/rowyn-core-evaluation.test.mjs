import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const topbarPath = new URL('../src/components/Topbar.tsx', import.meta.url);

test('search combobox exposes the rendered listbox and active keyboard option', async () => {
  const source = await readFile(topbarPath, 'utf8');
  assert.match(source, /aria-expanded=\{open && query\.trim\(\) !== ""\}/);
  assert.match(source, /aria-controls="hub-search-results"/);
  assert.match(source, /aria-activedescendant=\{results\.length > 0 \? `hub-search-option-\$\{Math\.min\(highlight, results\.length - 1\)\}` : undefined\}/);
  assert.match(source, /id="hub-search-results"/);
  // Section keys can contain spaces (for example, "App Store"). An ARIA
  // IDREF must be one token; use the result index rather than the raw key.
  assert.match(source, /id=\{`hub-search-option-\$\{index\}`\}/);
});
