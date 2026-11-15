// Regression: FilePick can be used inside New building's form without nesting forms
// or letting an import submission trigger building creation. No inference requests.
const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    const errors = [];
    let preparations = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && /form|hydration/i.test(message.text())) errors.push(message.text()); });
    await page.route('**/api/documents', route => route.fulfill({json: {
      id: 'form-check', filename: 'form-regression.pdf', format: 'pdf', page_count: 2,
      pages: [{number: 1}, {number: 2}], selectors: [], limitations: [],
    }}));
    await page.route('**/api/documents/form-check/sheets', route => {
      preparations++;
      return route.fulfill({json: {sheets: [{id: 'cubi',title: 'Form regression',thumbnail: 'api/drawings/cubi/image',source: {dataset: 'Upload',kind: 'Document page'}}],limitations: []}});
    });
    await page.goto('http://127.0.0.1:3011/home');
    assert.equal(await page.locator('form form').count(), 0);
    await page.getByRole('button', {name: 'New building', exact: true}).click();
    const building = page.getByRole('dialog', {name: 'New building', exact: true});
    await building.waitFor();
    await building.locator('input[type=file]').setInputFiles({name: 'form-regression.pdf',mimeType: 'application/pdf',buffer: Buffer.from('intercepted inventory fixture')});
    const importer = page.getByRole('dialog', {name: 'Import form-regression.pdf', exact: true});
    await importer.waitFor();
    assert.equal(await page.locator('form form').count(), 0);
    assert.equal(await importer.evaluate(node => node.parentElement === document.body), true);
    assert.equal(await importer.locator('input[type=checkbox]').count(), 2);
    await importer.locator('input[type=checkbox]').nth(1).uncheck();
    await importer.getByRole('button', {name: 'Prepare selected sheets'}).click();
    await building.getByRole('button', {name: 'form-regression.pdf', exact: true}).waitFor();
    assert.equal(await building.isVisible(), true);
    assert.equal(await building.locator('[role=alert]').innerText(), '');
    assert.equal(preparations, 0, 'Import must not submit the parent building form');
    // Cancellation and Escape return to the outer dialog as well.
    for (const close of ['cancel','escape']) {
      await building.locator('input[type=file]').setInputFiles({name: 'form-regression.pdf',mimeType: 'application/pdf',buffer: Buffer.from('intercepted inventory fixture')});
      await importer.waitFor();
      if (close === 'cancel') await importer.getByRole('button', {name: 'Cancel',exact: true}).click();
      else await page.keyboard.press('Escape');
      await importer.waitFor({state: 'detached'});
      assert.equal(await building.isVisible(), true);
    }
    await building.getByRole('textbox', {name: 'Name',exact: true}).fill('Form regression building');
    await building.getByRole('button', {name: 'Create building',exact: true}).click();
    await page.waitForURL(/\/building\?id=local-/);
    assert.equal(preparations, 1);
    assert.equal(await page.locator('form form').count(), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({checks: ['no nested forms','PDF import inside New building','isolated submit','cancel and Escape preserve parent dialog','building creation'],errors}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exit(1);});
