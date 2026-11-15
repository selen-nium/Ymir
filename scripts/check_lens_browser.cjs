// Browser QA uses the explicit no-inference backend in tests/browser_backend.py.
const { chromium }=require('../frontend/node_modules/playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url());
  await route.continue({url:'http://127.0.0.1:8012'+path.pathname+path.search});
 });
 await page.goto('http://127.0.0.1:3011/building?id=ac20');
 await page.getByRole('tab',{name:'Ask',exact:true}).click();
 await page.getByText('Ymir ready').waitFor();
 const scope=page.locator('.ask-scope');const checks=scope.locator('input');await checks.first().waitFor();
 assert.equal(await checks.count(),5);
 for(let i=0;i<5;i++){if(!(await checks.nth(i).isChecked()) && await checks.nth(i).isEnabled()){await checks.nth(i).check();}}
 assert.equal(await scope.locator('input:checked').count(),3);
 assert.equal(await scope.locator('input:disabled:not(:checked)').count(),2);
 await checks.first().uncheck();assert.equal(await checks.first().isChecked(),false);await checks.nth(3).check();
 await page.getByRole('textbox',{name:'Your question'}).fill('Count doors browser QA');
 await page.locator('button[aria-label="Ask"]').click();
 await page.getByRole('button',{name:'Reconnect',exact:true}).waitFor();
 await page.reload();
 await page.getByRole('tab',{name:'Ask',exact:true}).click();
 // A restored completed job still offers manual review; a running job replays its events.
 await page.locator('.msg.a').filter({hasText:/Needs review|Check the boxes/}).first().waitFor({timeout:20000});
 await page.waitForFunction(()=>document.querySelector('.review') || [...document.querySelectorAll('button')].some(b=>b.textContent.includes('Review labels · doors') && !b.disabled));
 if(!(await page.locator('.review').count()))await page.getByRole('button',{name:/Review labels · doors/}).last().click();
 await page.getByText('Check the boxes',{exact:false}).first().waitFor();
 assert.equal(await page.locator('.review').count(),1);
 assert(!/0% sure/.test(await page.locator('.review').innerText()));
 await page.getByRole('button',{name:'Save labels',exact:true}).click();
 await page.locator('.mark.ok').filter({hasText:'Labels saved'}).waitFor();
 await page.waitForFunction(()=>document.querySelectorAll('.review').length===0);
 await page.getByRole('button',{name:/^Show source /}).first().click();
 await page.screenshot({path:'/tmp/ymir-lens-workspace.png',fullPage:true});
 // PDF page import through the same FilePick used by home, rail and workspace.
 await page.getByRole('button',{name:'Add sheet',exact:false}).first().click();
 await page.locator('input[type=file]').first().setInputFiles('/tmp/ymir-browser-document.pdf');
 await page.getByRole('dialog').waitFor();
 const pages=page.getByRole('dialog').locator('input[type=checkbox]');assert.equal(await pages.count(),4);
 await pages.nth(2).uncheck();await pages.nth(3).uncheck();
 await page.getByRole('button',{name:'Prepare selected sheets'}).click();
 await page.getByText('ymir-browser-document · page 2',{exact:false}).first().waitFor({timeout:60000});
 await page.getByRole('tab',{name:'Ask',exact:true}).click();
 assert.equal(await page.locator('.ask-scope input').count(),7);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/ymir-lens-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({checks:['three-sheet scope','reload/reconnect','needs_review','review persisted','PDF page selection','mobile render'],errors,screenshots:['/tmp/ymir-lens-workspace.png','/tmp/ymir-lens-mobile.png']}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
