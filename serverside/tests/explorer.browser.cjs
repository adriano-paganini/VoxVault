const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '../static');

async function fixture(page) {
  let failDelete = false;
  let deletions = 0;
  const searches = [];
  let failSearch = false;
  let chunks = Array.from({ length: 26 }, (_, index) => ({
    id: index + 1, recordingId: index + 1, recordingTimestamp: 1700000000000 + index * 1000,
    chunkIndex: 0, language: 'en', text: `Conversation ${index + 1} about Thursday's meeting.`,
    wordCount: 6, startMs: 0, endMs: 2000, words: [],
    personId: index < 2 ? 1 : null, personName: index < 2 ? 'Morgan' : null,
    hasVoiceEmbedding: index !== 25, similarity: null,
  }));
  const people = [{ id: 1, name: 'Morgan' }, ...Array.from({ length: 29 }, (_, index) => ({
    id: index + 3, name: `Other ${String(index + 3).padStart(2, '0')}`,
  }))];
  chunks.push(...Array.from({ length: 30 }, (_, index) => ({
    ...chunks[0], id: 100 + index, recordingId: 1, chunkIndex: index + 1,
    startMs: (index + 1) * 2000, endMs: (index + 2) * 2000,
    text: index === 20 ? 'Funding the upcoming project. '.repeat(100) : `Context segment ${index + 1}. Full dialogue remains visible.`,
    personId: null, personName: null, hasVoiceEmbedding: index !== 29,
  })));
  const recordingIds = Array.from({ length: 26 }, (_, index) => index + 1);
  const conversation = (id, url) => {
    const segments = chunks.filter(chunk => chunk.recordingId === id).sort((a, b) => a.startMs - b.startMs);
    const query = url.searchParams.get('q') || '';
    const assignment = url.searchParams.get('assignment') || 'all';
    const matched = segments.filter(chunk => query &&
      (assignment === 'all' || (assignment === 'assigned') === (chunk.personId !== null)) &&
      (query === 'financial planning' || query.length === 2000 ? [110, 120].includes(chunk.id) : chunk.text.toLowerCase().includes(query.toLowerCase())));
    return { id, timestamp: 1700000000000 + id * 1000, language: 'en',
      chunkCount: segments.length, wordCount: segments.reduce((n, chunk) => n + chunk.wordCount, 0),
      durationMs: segments.at(-1)?.endMs || 0, personCount: new Set(segments.map(chunk => chunk.personId).filter(Boolean)).size,
      preview: segments[0]?.text || '', similarity: query ? 0.9 : null, matchScore: query ? matched.length * 0.9 : null,
      matchedChunkIds: matched.map(chunk => chunk.id),
      chunks: segments.map(chunk => ({ ...chunk, matched: matched.includes(chunk) })),
    };
  };
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
    if (endpoint === '/conversations') {
      assert.equal(url.searchParams.has('mode'), false);
      assert.equal(url.searchParams.has('min_similarity'), false);
      searches.push(Object.fromEntries(url.searchParams));
      if (failSearch && url.searchParams.get('q')) return json({ detail: 'Semantic search temporarily unavailable.' }, 503);
      let items = recordingIds.map(id => conversation(id, url)).reverse();
      if (url.searchParams.get('q')) items = items.filter(item => item.matchedChunkIds.length);
      return json(paginate(items, url));
    }
    const conversationMatch = endpoint.match(/^\/conversations\/(\d+)$/);
    if (conversationMatch) {
      assert.equal(url.searchParams.has('mode'), false);
      assert.equal(url.searchParams.has('min_similarity'), false);
      const id = Number(conversationMatch[1]);
      return recordingIds.includes(id) ? json(conversation(id, url)) : json({ detail: 'Conversation not found.' }, 404);
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
        const person = { id: Math.max(...people.map(person => person.id)) + 1, name };
        people.push(person);
        const chunk = chunks.find(item => item.id === chunkId);
        Object.assign(chunk, { personId: person.id, personName: name });
        return json({ chunk, person: profile(person) }, 201);
      }
      const query = (url.searchParams.get('q') || '').toLowerCase();
      const items = people.map(profile).filter(person => person.name.toLowerCase().startsWith(query))
        .sort((a, b) => (b.similarity ?? -2) - (a.similarity ?? -2) || a.name.localeCompare(b.name));
      return json(paginate(items, url));
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
      if (endpoint === '/chunks') searches.push(Object.fromEntries(url.searchParams));
      let items = [...chunks].reverse();
      const assignment = url.searchParams.get('assignment');
      if (assignment === 'assigned') items = items.filter(chunk => chunk.personId !== null);
      if (assignment === 'unassigned') items = items.filter(chunk => chunk.personId === null);
      if (endpoint === '/similar') items = items.filter(chunk => chunk.hasVoiceEmbedding && chunk.id !== Number(url.searchParams.get('chunk_id')));
      return json(paginate(items, url));
    }
    throw new Error(`Unexpected request: ${request.method()} ${url.pathname}`);
  });
  return { failDeletion: value => { failDelete = value; }, failSearch: value => { failSearch = value; }, deletionCount: () => deletions, searches };
}

async function fits(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.equal(await page.locator('#inspector').evaluate(node => node.open && node.scrollWidth > node.clientWidth), false);
  assert.equal(await page.locator('#speaker-picker').evaluate(node => node.open && node.scrollWidth > node.clientWidth), false);
}

async function run(browser, viewport) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const data = await fixture(page);
  await page.goto('http://voxvault.test/ui/explorer?app=1');
  await page.locator('#conversations-results [data-recording-id="26"]').waitFor();
  assert.equal(await page.locator('#conversations-results .conversation-row').count(), 25);
  assert.equal(await page.locator('#search-assignment').inputValue(), 'all');
  assert.equal(await page.locator('#search-mode').count(), 0);
  assert.equal(await page.locator('.explorer-heading').isVisible(), false);
  await fits(page);
  if (process.env.VOXVAULT_SCREENSHOT_DIR) {
    await fs.mkdir(process.env.VOXVAULT_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(process.env.VOXVAULT_SCREENSHOT_DIR, `explorer-${viewport.width}.png`) });
  }
  await page.locator('#conversations-pagination button[title="Next page"]').click();
  await page.locator('#conversations-results [data-recording-id="1"]').click();
  await page.locator('#segment-129').waitFor();
  assert.equal(await page.locator('.dialogue-segment').count(), 31);
  const times = await page.locator('.dialogue-segment').evaluateAll(nodes => nodes.map(node => Number(node.dataset.chunkId)));
  assert.deepEqual(times, [1, ...Array.from({ length: 30 }, (_, i) => 100 + i)]);
  await page.locator('#back-to-results').click();
  await page.locator('#conversations-results [data-recording-id="1"]').waitFor();
  assert.equal(await page.locator('#conversations-results .conversation-row').count(), 1);

  await page.locator('#search-tab').click();
  await page.locator('#search-query').fill('financial planning');
  await page.locator('#search-submit').click();
  await page.waitForFunction(() => document.querySelector('#search-summary').textContent === '1 conversation found');
  assert.equal(data.searches.at(-1).mode, undefined);
  await page.locator('#search-results [data-recording-id="1"]').click();
  await page.locator('#segment-120.matched').waitFor();
  assert.equal(await page.locator('.dialogue-segment').count(), 31);
  assert.equal(await page.locator('.dialogue-segment.matched').count(), 2);
  assert.equal(await page.locator('#segment-110').evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.top >= 0 && rect.top < innerHeight && document.activeElement === node;
  }), true);
  assert.equal(await page.locator('#segment-110 .transcript').evaluate(node => parseFloat(getComputedStyle(node).fontSize) >= 18), true);
  assert.equal(await page.locator('#segment-120 .transcript').textContent(), 'Funding the upcoming project. '.repeat(100));
  await page.locator('#next-match').click();
  await page.waitForFunction(() => document.querySelector('#match-summary').textContent.startsWith('Match 2 of 2'));
  await page.waitForFunction(() => {
    const rect = document.querySelector('#segment-120').getBoundingClientRect();
    return rect.top >= 0 && rect.top < innerHeight;
  });
  assert.equal(await page.locator('#next-match').isDisabled(), true);
  await fits(page);
  if (process.env.VOXVAULT_SCREENSHOT_DIR) {
    await page.screenshot({ path: path.join(process.env.VOXVAULT_SCREENSHOT_DIR, `conversation-${viewport.width}.png`) });
  }
  await page.reload();
  await page.locator('#segment-120.matched').waitFor();
  assert.equal(await page.locator('.dialogue-segment').count(), 31);
  await page.evaluate(() => window.voxvaultBack());
  await page.locator('#search-view:not(.hidden)').waitFor();
  assert.equal(await page.locator('#search-query').inputValue(), 'financial planning');

  await page.locator('#search-query').fill('Thursday');
  await page.locator('#search-submit').click();
  await page.waitForFunction(() => document.querySelector('#search-summary').textContent === '26 conversations found');
  await page.locator('#search-pagination button[title="Next page"]').click();
  await page.locator('#search-results [data-recording-id="1"]').click();
  await page.locator('#segment-1 mark.text-match').waitFor();
  assert.equal(await page.locator('#segment-1 mark.text-match').textContent(), 'Thursday');

  await page.locator('#segment-129 .speaker-badge').click();
  await page.locator('#speaker-options [data-person-id="1"]').waitFor();
  assert.equal(await page.locator('#speaker-status').textContent(), 'No voice comparison available');
  await page.locator('#speaker-pagination button[title="Next page"]').click();
  await page.locator('#speaker-options [data-person-id="31"]').waitFor();
  await page.locator('#speaker-filter').fill('Morgan');
  await page.locator('#speaker-options [data-person-id="1"]').waitFor();
  await page.locator('#speaker-options [data-person-id="1"]').click();
  await page.waitForFunction(() => !document.querySelector('#speaker-picker').open);
  assert.equal(await page.locator('#segment-129 .speaker-badge').textContent(), 'Morgan');
  await page.locator('#segment-129 .speaker-badge').click();
  await page.locator('#speaker-unassign').click();
  await page.waitForFunction(() => !document.querySelector('#speaker-picker').open);
  assert.equal(await page.locator('#segment-129 .speaker-badge').textContent(), 'Assign speaker');

  await page.locator('#segment-120 .speaker-badge').click();
  await page.locator('#speaker-new-name').fill('A'.repeat(200));
  await page.locator('#speaker-create-form button').click();
  await page.waitForFunction(() => !document.querySelector('#speaker-picker').open);
  await fits(page);
  await page.locator('#segment-120 .speaker-badge').click();
  await page.locator('#speaker-options [data-person-id="32"]').waitFor();
  await fits(page);
  if (process.env.VOXVAULT_SCREENSHOT_DIR) {
    await page.screenshot({ path: path.join(process.env.VOXVAULT_SCREENSHOT_DIR, `speaker-${viewport.width}.png`) });
  }
  await page.evaluate(() => window.voxvaultBack());
  assert.equal(await page.locator('#speaker-picker').isVisible(), false);

  await page.locator('#segment-120').getByRole('button', { name: 'Inspect voice' }).click();
  await page.locator('#person-profile:not(.hidden)').waitFor();
  await fits(page);
  await page.locator('#person-name').fill('Taylor');
  await page.locator('#save-person-name').click();
  await page.waitForFunction(() => document.querySelector('#inspector-status').textContent === 'Person name saved.');
  await page.locator('#close-inspector').click();
  assert.equal(await page.locator('#segment-120 .speaker-badge').textContent(), 'Taylor');

  await page.locator('#segment-1 .danger-icon').click();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.equal(data.deletionCount(), 0);
  data.failDeletion(true);
  await page.locator('#segment-1 .danger-icon').click();
  await page.locator('#delete-confirmation button[value="delete"]').click();
  await page.locator('#page-error:not(.hidden)').waitFor();
  assert.equal(await page.locator('.dialogue-segment').count(), 31);
  data.failDeletion(false);
  await page.locator('#segment-1 .danger-icon').click();
  await page.locator('#delete-confirmation button[value="delete"]').click();
  await page.waitForFunction(() => document.querySelector('#page-status').textContent === 'Chunk #1 deleted.');
  assert.equal(await page.locator('.dialogue-segment').count(), 30);
  assert.equal(await page.locator('.dialogue-segment.matched').count(), 0);

  await page.locator('#back-to-results').click();
  await page.locator('#search-view:not(.hidden)').waitFor();
  data.failSearch(true);
  await page.locator('#search-submit').click();
  await page.locator('#page-error:not(.hidden)').waitFor();
  assert.equal(await page.locator('#search-results .conversation-row').count(), 0);
  data.failSearch(false);
  await page.locator('#search-submit').click();
  await page.waitForFunction(() => document.querySelector('#search-summary').textContent === '25 conversations found');
  await page.locator('#search-query').fill('x'.repeat(2000));
  await page.locator('#search-submit').click();
  await page.waitForFunction(() => document.querySelector('#search-summary').textContent === '1 conversation found');
  await page.locator('#search-results [data-recording-id="1"]').click();
  await page.locator('#segment-110.matched').waitFor();
  assert.equal(await page.locator('#match-navigation').evaluate(node => node.getBoundingClientRect().height < 110), true);
  assert.equal(await page.locator('#segment-110').evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.top >= document.querySelector('#match-navigation').getBoundingClientRect().bottom && rect.top < innerHeight;
  }), true);
  await fits(page);
  assert.deepEqual(errors, []);
  await page.close();
  console.log(`PASS ${viewport.width}x${viewport.height}: conversation navigation, semantic search, highlights, speaker pagination/assignment, deletion, errors, layout`);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 320, height: 720 }, { width: 390, height: 844 }, { width: 1280, height: 900 }]) await run(browser, viewport);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
