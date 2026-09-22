const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '../static');
const output = process.env.APPEARANCE_SCREENSHOTS;
const types = { '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml', '.html': 'text/html' };

async function run(browser, viewport, view) {
  const page = await browser.newPage({ viewport, colorScheme: 'light' });
  await page.route('http://voxvault.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/api/')) {
      if (url.pathname === '/api/setup/status') return route.fulfill({ json: {
        stage: 'keys', keysExist: false, history: [], expectedLanguages: ['en'],
      } });
      if (url.pathname === '/api/setup/languages') return route.fulfill({ json: { languages: [{ code: 'en', name: 'English' }] } });
      if (url.pathname === '/api/explorer/conversations') return route.fulfill({ json: { items: [], total: 0, page: 1, pageSize: 20 } });
      if (url.pathname === '/api/explorer/persons') return route.fulfill({ json: { items: [], total: 0 } });
      return route.fulfill({ status: 503, json: { detail: 'Preview data unavailable.' } });
    }
    const file = url.pathname.startsWith('/static/') ? url.pathname.slice(8) : `${view}.html`;
    return route.fulfill({ body: await fs.readFile(path.join(staticRoot, file)), contentType: types[path.extname(file)] });
  });
  await page.goto(`http://voxvault.test/ui/${view}`);
  await page.locator('.brand-icon').evaluate(img => img.decode());
  for (const colorScheme of ['light', 'dark', 'light']) {
    await page.emulateMedia({ colorScheme });
    const appearance = await page.evaluate(async () => {
      const logo = document.querySelector('.brand-icon');
      const canvas = document.createElement('canvas');
      canvas.width = logo.naturalWidth;
      canvas.height = logo.naturalHeight;
      const ctx = canvas.getContext('2d');
      ctx.filter = getComputedStyle(logo).filter;
      ctx.drawImage(logo, 0, 0);
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let transparent = 0;
      let solid = 0;
      let min = 255;
      let max = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] === 0) transparent++;
        if (pixels[i + 3] > 240) {
          solid++;
          min = Math.min(min, pixels[i]);
          max = Math.max(max, pixels[i]);
        }
      }
      const root = getComputedStyle(document.documentElement);
      const rgb = value => {
        ctx.fillStyle = value;
        ctx.fillRect(0, 0, 1, 1);
        return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
      };
      const luminance = color => rgb(color).map(v => v / 255).map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
        .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
      ctx.filter = 'none';
      const contrast = (fg, bg) => {
        const a = luminance(root.getPropertyValue(fg).trim());
        const b = luminance(root.getPropertyValue(bg).trim());
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      };
      const pairs = [['--text', '--surface'], ['--muted', '--surface'], ['--subtle', '--panel'],
        ['--on-accent', '--accent'], ['--on-accent', '--accent-hover'], ['--success-text', '--success-bg'],
        ['--error-text', '--error-bg'], ['--warning-text', '--warning-bg'], ['--info-text', '--info-bg']];
      const favicon = new Image();
      favicon.src = document.querySelector('link[type="image/svg+xml"]').href;
      await favicon.decode();
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(favicon, 0, 0, 128, 128);
      const faviconPixels = ctx.getImageData(0, 0, 128, 128).data;
      const faviconSolid = [];
      for (let i = 0; i < faviconPixels.length; i += 4) {
        if (faviconPixels[i + 3] > 240) faviconSolid.push(faviconPixels[i]);
      }
      return { transparent, solid, min, max, area: pixels.length / 4,
        width: logo.getBoundingClientRect().width,
        background: root.backgroundColor,
        overflow: document.documentElement.scrollWidth > innerWidth,
        contrasts: pairs.map(pair => ({ pair, value: contrast(...pair) })),
        faviconMin: Math.min(...faviconSolid), faviconMax: Math.max(...faviconSolid),
        qrBackground: document.querySelector('.qr') && getComputedStyle(document.querySelector('.qr')).backgroundColor,
      };
    });
    assert(appearance.transparent > appearance.area * 0.7, 'Logo background and enclosed spaces must be transparent');
    assert(appearance.solid > appearance.area * 0.05, 'Logo must contain visible artwork');
    assert.equal(appearance.min, colorScheme === 'dark' ? 255 : 0);
    assert.equal(appearance.max, colorScheme === 'dark' ? 255 : 0);
    assert.equal(appearance.faviconMin, colorScheme === 'dark' ? 255 : 0);
    assert.equal(appearance.faviconMax, colorScheme === 'dark' ? 255 : 0);
    assert(appearance.width <= 28, 'Branding must stay compact');
    assert.equal(appearance.overflow, false);
    assert.equal(appearance.background, colorScheme === 'dark' ? 'rgb(20, 25, 27)' : 'rgb(247, 249, 250)');
    if (appearance.qrBackground) assert.equal(appearance.qrBackground, 'rgb(255, 255, 255)');
    for (const { pair, value } of appearance.contrasts) assert(value >= 4.5, `${colorScheme} ${pair}: contrast ${value}`);
    if (output) await page.screenshot({ path: path.join(output, `${view}-${viewport.width}-${colorScheme}.png`), fullPage: true });
  }
  if (view === 'explorer') {
    await page.goto('http://voxvault.test/ui/explorer?app=1');
    assert.equal(await page.locator('.explorer-heading').isVisible(), false);
  }
  await page.close();
}

(async () => {
  if (output) await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 320, height: 700 }]) {
      for (const view of ['index', 'explorer']) await run(browser, viewport, view);
    }
    console.log('Appearance checks passed: transparent artwork, live theme changes, favicon, contrast, compact layout.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
