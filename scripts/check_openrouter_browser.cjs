// UI integration uses tests/browser_backend.py; no paid language-model calls.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const origin=process.env.YMIR_TEST_FRONTEND || 'http://127.0.0.1:3013';
const backend=process.env.YMIR_TEST_BACKEND || 'http://127.0.0.1:8012';
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage(),errors=[],requests=[];
  let frontierUnavailable=false;
  const namespace='-openrouter-'+Date.now();
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url());
   let postData;
   if(url.pathname==='/api/ask' && req.method()==='POST'){const body=req.postDataJSON();requests.push(body);postData=JSON.stringify({...body,project_id:body.project_id+namespace,conversation_id:body.conversation_id+namespace});}
   if(url.pathname==='/api/lens/jobs'){for(const key of ['project_id','conversation_id'])url.searchParams.set(key,url.searchParams.get(key)+namespace);}
   if(url.pathname==='/api/lens/status') {
    const res=await route.fetch({url:backend+url.pathname});const data=await res.json();
    if(frontierUnavailable)data.modes.frontier.ready=false;
    await route.fulfill({json:data});return;
   }
   await route.continue({url:backend+url.pathname+url.search,...(postData?{postData}: {})});
  });
  await page.goto(origin+'/building?id=ac20');
  await page.getByRole('tab',{name:'Ask',exact:true}).click();
  const selector=page.getByRole('combobox',{name:'Response mode'}),question=page.getByRole('textbox',{name:'Your question'}),send=page.getByRole('button',{name:'Ask',exact:true});
  await page.getByText('Smart ready',{exact:true}).waitFor();
  assert.equal(await selector.inputValue(),'smart');
  assert.deepEqual(await selector.locator('option').allTextContents(),['Smart','Frontier']);
  const scope=await page.locator('.ask-scope input:checked').count();
  // Keyboard focus, native selection, and tab order immediately beside Send.
  await question.focus();await page.keyboard.press('Tab');assert(await selector.evaluate(e=>e===document.activeElement));
  await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');assert.equal(await selector.inputValue(),'frontier');
  assert.equal(await page.locator('.ask-scope input:checked').count(),scope);
  await selector.selectOption('smart');await question.fill('Count doors in Smart');await send.click();
  assert.equal(requests.at(-1).routing_mode,'smart');
  assert(await selector.isDisabled());
  await page.locator('.review').waitFor({timeout:20000});
  await page.getByRole('button',{name:'Skip',exact:true}).click();
  await selector.selectOption('frontier');
  await page.locator('.sugg button').first().click();
  assert.equal(requests.at(-1).routing_mode,'frontier');
  assert.deepEqual(requests[0].selected_drawing_ids,requests[1].selected_drawing_ids);
  await page.getByRole('button',{name:'Reconnect',exact:true}).waitFor();
  await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();
  assert.equal(await selector.inputValue(),'frontier');
  await page.waitForFunction(()=>document.querySelector('.review') || !document.querySelector('select[aria-label="Response mode"]').disabled,{timeout:20000});
  if(!(await page.locator('.review').count()))await page.getByRole('button',{name:/Review labels/}).last().click();
  await page.locator('.review').waitFor({timeout:20000});
  await page.getByRole('button',{name:'Skip',exact:true}).click();
  assert.equal(requests.length,2); // Reload/reconnect never sends another question.
  assert((await page.locator('.msg.q .scope').nth(0).innerText()).startsWith('Smart ·'));
  assert((await page.locator('.msg.q .scope').nth(1).innerText()).startsWith('Frontier ·'));
  const answer=page.locator('.msg.a[role=button]').last();await answer.click();
  if(await page.getByRole('button',{name:'Expand the trace'}).isVisible())await page.getByRole('button',{name:'Expand the trace'}).click();
  await page.locator('.tnode').filter({hasText:'Sol'}).first().waitFor();
  assert(await page.locator('.tnode').filter({hasText:'Argon'}).count()>0);
  const desktop=await selector.boundingBox(),button=await send.boundingBox();
  assert(desktop.x+desktop.width<=button.x && Math.abs(desktop.y-button.y)<10);
  assert(!/\$\d|remaining|held|budget|balance/i.test(await page.locator('.askp').innerText()));
  await page.screenshot({path:'/tmp/ymir-openrouter-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  const mobile=await selector.boundingBox(),mobileButton=await send.boundingBox();
  assert(mobile.x+mobile.width<=mobileButton.x && mobileButton.x+mobileButton.width<=390);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/ymir-openrouter-mobile.png',fullPage:true});
  frontierUnavailable=true;await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByText('Frontier unavailable · Try again when it is ready.',{exact:true}).waitFor();
  assert.equal(await selector.inputValue(),'frontier');assert(await selector.locator('option[value=frontier]').isDisabled());
  await question.fill('Unavailable mode');assert(await send.isDisabled());
  await selector.selectOption('smart');await page.getByText('Smart ready',{exact:true}).waitFor();assert(await send.isEnabled());
  await page.goto(origin+'/drawing?id=cubi');await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByText('Smart ready',{exact:true}).waitFor();assert.equal(await selector.inputValue(),'smart');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:['both modes beside Send','keyboard access','per-chat default/persistence','switch preserves drawing selection','suggestions snapshot mode','selector locked while active','reload/reconnect without inference replay','recorded source labels','Sol/Argon trace identities','unavailable route isolation','mobile layout','no budget displays'],requests:requests.length,errors,screenshots:['/tmp/ymir-openrouter-desktop.png','/tmp/ymir-openrouter-mobile.png']}));
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
