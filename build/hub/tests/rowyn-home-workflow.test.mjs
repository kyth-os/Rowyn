import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

test('Home renders one workspace card for every manifest destination', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rowyn-home-workflow-'));
  try {
    const outfile = resolve(directory, 'home.cjs');
    await build({
      stdin: {
        contents: `import React from 'react';
          import { renderToStaticMarkup } from 'react-dom/server';
          import { StaticRouter } from 'react-router-dom';
          import { Dashboard } from './src/pages/Dashboard';
          export function renderHome() {
            return renderToStaticMarkup(<StaticRouter location="/"><Dashboard /></StaticRouter>);
          }`,
        resolveDir: root,
        loader: 'tsx',
      },
      outfile, bundle: true, platform: 'node', format: 'cjs',
      jsx: 'automatic', loader: { '.css': 'empty' }, logLevel: 'silent',
    });
    const loaded = await import(pathToFileURL(outfile).href);
    const html = (loaded.default ?? loaded).renderHome();
    const manifest = JSON.parse(await readFile(resolve(root, 'src/data/hubRoutes.json'), 'utf8'));
    const workspace = html.match(/<div class="rowyn-home-destinations">([\s\S]*?)<\/section>/)?.[1];
    assert.ok(workspace, 'the actual Home workspace section must render');
    const names = [...workspace.matchAll(/<strong>([^<]+)<\/strong>/g)].map((match) => match[1]);
    assert.deepEqual(names.toSorted(), manifest.destinations.map((destination) => destination.key).toSorted());
    assert.equal(new Set(names).size, names.length, 'workspace cards must not be duplicated');
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
