const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '../static');

async function fixture(page) {
  let failDelete = false;
  let deletions = 0;
  let chunks = Array.from({ length: 26 }, (_, index) => ({
    id: index + 1, recordingId: index + 1, recordingTimestamp: 1700000000000 + index * 1000,
    chunkIndex: 0, language: 'en', text: `Conversation ${index + 1} about Thursday's meeting.`,
    wordCount: 6, startMs: 0, endMs: 2000, words: [],
    personId: index < 2 ? 1 : null, personName: index < 2 ? 'Morgan' : null,
    hasVoiceEmbedding: index !== 25, similarity: null,
  }));
  const people = [{ id: 1, name: 'Morgan' }];
  const profile = person => {
    const known = chunks.filter(chunk => chunk.personId === person.id);
    return { ...person, chunkCount: known.length, recordingCount: known.length,
      wordCount: known.length * 6, hasVoiceEmbedding: !!known.length, similarity: known.length ? 0.95 : null };
  };
  const paginate = (items, url) => {
    const limit = Number(url.searchParams.get('limit') || 25);
    const offset = Number(url.searchParams.get('offset') || 0);
    return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
  };
  await page.route('http://voxvault.test/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.replace('/api/explorer', '');
    const json = (body, status = 200) => route.fulfill({ status, json: body });
    if (!url.pathname.startsWith('/api/')) {
      const file = url.pathname.startsWith('/static/') ? url.pathname.slice(8) : 'explorer.html';
      const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
      return route.fulfill({ body: await fs.readFile(path.join(staticRoot, file)), contentType });
    }
    const chunkMatch = endpoint.match(/^\/chunks\/(\d+)(\/person)?$/);
    if (chunkMatch) {
      const chunk = chunks.find(item => item.id === Number(chunkMatch[1]));
      if (!chunk) return json({ detail: 'Conversation chunk not found.' }, 404);
      if (request.method() === 'DELETE') {
        deletions++;
        if (failDelete) return json({ detail: 'Database unavailable.' }, 503);
        chunks = chunks.filter(item => item.id !== chunk.id);
        return route.fulfill({ status: 204 });
      }
      if (request.method() === 'PUT') {
        chunk.personId = request.postDataJSON().personId;
        const person = people.find(item => item.id === chunk.personId);
        chunk.personName = person?.name || null;
        return json({ chunk, person: person ? profile(person) : null });
      }
      return json(chunk);
    }
    if (endpoint === '/persons') {
      if (request.method() === 'POST') {
        const { name, chunkId } = request.postDataJSON();
        const person = { id: people.length + 1, name };
        people.push(person);
        const chunk = chunks.find(item => item.id === chunkId);
        Object.assign(chunk, { personId: person.id, personName: name });
        return json({ chunk, person: profile(person) }, 201);
      }
      return json({ items: people.map(profile) });
    }
    const personMatch = endpoint.match(/^\/persons\/(\d+)(\/chunks)?$/);
    if (personMatch) {
      const person = people.find(item => item.id === Number(personMatch[1]));
      if (personMatch[2]) return json(paginate(chunks.filter(chunk => chunk.personId === person.id), url));
      if (request.method() === 'PATCH') {
        person.name = request.postDataJSON().name;
        chunks.filter(chunk => chunk.personId === person.id).forEach(chunk => { chunk.personName = person.name; });
      }
      return json(profile(person));
    }
    if (endpoint === '/chunks' || endpoint === '/similar') {
      let items = [...chunks].reverse();
      const assignment = url.searchParams.get('assignment');
      if (assignment === 'assigned') items = items.filter(chunk => chunk.personId !== null);
      if (assignment === 'unassigned') items = items.filter(chunk => chunk.personId === null);
      if (endpoint === '/similar') items = items.filter(chunk => chunk.hasVoiceEmbedding && chunk.id !== Number(url.searchParams.get('chunk_id')));
      return json(paginate(items, url));
    }
    throw new Error(`Unexpected request: ${request.method()} ${url.pathname}`);
  });
  return { failDeletion: value => { failDelete = value; }, deletionCount: () => deletions };
}

async function fits(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.equal(await page.locator('#inspector').evaluate(node => node.open && node.scrollWidth > node.clientWidth), false);
}

async function run(browser, viewport) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const data = await fixture(page);
  await page.goto('http://voxvault.test/ui/explorer?app=1');
  await page.waitForFunction(() => document.querySelector('#search-results').children.length === 25);
  await fits(page);
  assert.equal(await page.locator('.explorer-heading').isVisible(), false);
  await page.getByRole('button', { name: 'Next page', exact: true }).click();
  await page.locator('#search-results [data-chunk-id="1"]').waitFor();
  await page.getByRole('button', { name: 'Delete chunk #1', exact: true }).click();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.equal(data.deletionCount(), 0);

  data.failDeletion(true);
  await page.getByRole('button', { name: 'Delete chunk #1', exact: true }).click();
  await page.locator('#delete-confirmation button[value="delete"]').click();
  await page.locator('#page-error:not(.hidden)').waitFor();
  assert.equal(await page.locator('#search-results article').count(), 1);
  data.failDeletion(false);
  await page.getByRole('button', { name: 'Delete chunk #1', exact: true }).click();
  await page.locator('#delete-confirmation button[value="delete"]').click();
  await page.waitForFunction(() => document.querySelector('#page-status').textContent === 'Chunk #1 deleted.');
  assert.equal(await page.locator('#search-results article').count(), 25);
  assert.equal(await page.locator('#search-pagination .previous').isDisabled(), true);

  await page.locator('#search-results [data-chunk-id="2"] button').first().click();
  await page.locator('#person-profile:not(.hidden)').waitFor();
  await page.waitForFunction(() => document.querySelector('#person-statistics').textContent.includes('1 confirmed chunk'));
  await fits(page);
  await page.locator('#selected-chunk .danger-icon').click();
  await page.locator('#delete-confirmation button[value="delete"]').click();
  await page.waitForFunction(() => document.querySelector('#inspector-status').textContent === 'Chunk #2 deleted.');
  assert.equal(await page.locator('#selected-chunk-section').isVisible(), false);
  assert.match(await page.locator('#person-statistics').textContent(), /0 confirmed chunks/);
  assert.equal(await page.locator('#candidate-summary').textContent(), 'No voice embedding selected.');
  await page.locator('#close-inspector').click();

  await page.locator('#search-results [data-chunk-id="26"] .danger-icon').click();
  await page.locator('#delete-confirmation button[value="delete"]').click();
  await page.waitForFunction(() => document.querySelector('#page-status').textContent === 'Chunk #26 deleted.');
  await page.locator('#search-results [data-chunk-id="25"] button').first().click();
  await page.locator('#new-person-name').fill('Taylor');
  await page.locator('#create-person-button').click();
  await page.waitForFunction(() => document.querySelector('#inspector-status').textContent.includes('Taylor created'));
  await page.locator('#person-name').fill('A'.repeat(200));
  await page.locator('#save-person-name').click();
  await page.waitForFunction(() => document.querySelector('#inspector-status').textContent === 'Person name saved.');
  await fits(page);
  await page.locator('#unassign-selected').click();
  await page.waitForFunction(() => document.querySelector('#inspector-status').textContent.includes('now unassigned'));
  await page.locator('#assign-selected').click();
  await page.waitForFunction(() => document.querySelector('#inspector-status').textContent.includes('Voice profile updated.'));
  await fits(page);
  assert.deepEqual(errors, []);
  await page.close();
  console.log(`PASS ${viewport.width}x${viewport.height}: deletion, cancellation, failure, pagination, profiles, assignment, layout`);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 320, height: 720 }, { width: 390, height: 844 }, { width: 1280, height: 900 }]) await run(browser, viewport);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
