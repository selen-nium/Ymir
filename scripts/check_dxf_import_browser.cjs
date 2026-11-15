// Uses the local API and a caller-supplied analytic DXF fixture; no inference.
const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
const origin = process.env.YMIR_TEST_FRONTEND || 'http://127.0.0.1:3021';
const fixture = process.env.YMIR_DXF_FIXTURE || '/tmp/ymir-test.dxf';
(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  try {
    await page.goto(origin+'/home');
    await page.locator('input[type=file]').first().setInputFiles(fixture);
    const dialog=page.getByRole('dialog'); await dialog.waitFor();
    await dialog.getByRole('checkbox').first().check();
    await dialog.getByRole('combobox',{name:'Drawing units'}).selectOption('m');
    await page.screenshot({path:'/tmp/ymir-dxf-import.png'});
    const prepared=page.waitForResponse(r=>r.url().endsWith('/sheets') && r.request().method()==='POST');
    await dialog.getByRole('button',{name:'Prepare selected sheets'}).click();
    const response=await prepared;
    assert.equal(response.status(),200,await response.text());
    assert.equal(response.request().postDataJSON().options.dxf_unit,'m');
    const result=await response.json(); assert.equal(result.sheets.length,1);
    await page.waitForURL(/drawing/);
    await page.reload();
    await page.getByText('File type: DXF',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({checks:['DXF selection dialog','confirmed units reach prepare','native sheet import','reload preserves DXF source format'],errors}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
