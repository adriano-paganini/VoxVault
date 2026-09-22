const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

async function run(browser, viewport) {
  const page = await browser.newPage({ viewport, colorScheme: process.env.COLOR_SCHEME || 'light' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let expectedLanguages = ['en'];
  let stage = 'device';
  let rejectSave = false;
  await page.route('http://voxvault.test/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) {
      const file = url.pathname.startsWith('/static/') ? url.pathname.slice(8) : 'index.html';
      const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
      return route.fulfill({ body: await fs.readFile(path.join(__dirname, '../static', file)), contentType });
    }
    if (url.pathname === '/api/setup/status') {
      return route.fulfill({ json: { stage, keysExist: true, expectedLanguages,
        history: [], readingText: 'The English enrollment passage.', publicUrl: '' } });
    }
    if (url.pathname === '/api/setup/languages') {
      if (request.method() === 'PUT') {
        if (rejectSave) return route.fulfill({ status: 503, json: { detail: 'Could not save languages.' } });
        expectedLanguages = request.postDataJSON().expectedLanguages;
        return route.fulfill({ json: { expectedLanguages } });
      }
      return route.fulfill({ json: { languages: [
        { code: 'en', name: 'English' }, { code: 'de', name: 'German' }, { code: 'fr', name: 'French' },
      ] } });
    }
    if (url.pathname === '/api/setup/step') {
      stage = request.postDataJSON().step;
      return route.fulfill({ json: { stage, expectedLanguages, readingText: 'The English enrollment passage.' } });
    }
    if (url.pathname === '/api/keys/public') return route.fulfill({ json: { publicKey: 'test-public-key' } });
    if (url.pathname === '/api/keys/qrcode') return route.fulfill({ status: 204 });
    throw new Error(`Unexpected request: ${request.method()} ${url.pathname}`);
  });

  await page.goto('http://voxvault.test/ui');
  await page.locator('#device-view:not(.hidden)').waitFor();
  await page.locator('#selected-languages').click();
  await page.getByLabel('German', { exact: true }).check();
  await page.waitForTimeout(1200);
  assert.equal(await page.getByLabel('German', { exact: true }).isChecked(), true);
  await page.getByRole('button', { name: 'Save languages', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#languages-status').textContent === 'Languages saved.');
  assert.deepEqual(expectedLanguages, ['en', 'de']);
  await page.reload();
  await page.locator('#device-view:not(.hidden)').waitFor();
  await page.locator('#selected-languages').click();
  assert.equal(await page.getByLabel('German', { exact: true }).isChecked(), true);

  await page.getByLabel('German', { exact: true }).uncheck();
  await page.getByLabel('English', { exact: true }).uncheck();
  await page.getByRole('button', { name: 'Save languages', exact: true }).click();
  await page.getByText('Select at least one spoken language.', { exact: true }).waitFor();
  assert.deepEqual(expectedLanguages, ['en', 'de']);
  await page.getByLabel('German', { exact: true }).check();
  rejectSave = true;
  await page.getByRole('button', { name: 'Save languages', exact: true }).click();
  await page.getByText('Could not save languages.', { exact: true }).waitFor();
  rejectSave = false;
  await page.getByRole('button', { name: 'Next step', exact: true }).click();
  await page.locator('#reading-view:not(.hidden)').waitFor();
  assert.deepEqual(expectedLanguages, ['de']);
  assert.equal(await page.locator('#other-reading-instructions').isVisible(), true);
  assert.equal(await page.locator('#english-reading-instructions').isVisible(), false);
  await page.getByLabel('English', { exact: true }).check();
  await page.getByRole('button', { name: 'Save languages', exact: true }).click();
  await page.locator('#english-reading-instructions:not(.hidden)').waitFor();
  assert.equal(await page.locator('#reading-text').textContent(), 'The English enrollment passage.');

  stage = 'complete';
  await page.reload();
  await page.locator('#complete-view:not(.hidden)').waitFor();
  await page.locator('#selected-languages').click();
  await page.getByLabel('English', { exact: true }).check();
  await page.getByRole('button', { name: 'Save languages', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#languages-status').textContent === 'Languages saved.');
  assert.deepEqual(expectedLanguages, ['en', 'de']);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: `/tmp/voxvault-setup-${viewport.width}.png`, fullPage: true });
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      await run(browser, viewport);
    }
    console.log('Setup language browser checks passed at desktop and mobile sizes.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
