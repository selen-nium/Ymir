// No paid inference: this script only talks to tests/browser_backend.py on :8012.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(20000);
 const errors=[];let dispatches=0;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const u=new URL(route.request().url());if(u.pathname==='/api/ask')dispatches++;
  await route.continue({url:'http://127.0.0.1:8012'+u.pathname+u.search});
 });
 const question=`Count doors for label editing QA ${Date.now()}`;
 const entry=()=>page.locator('.msg.q').filter({hasText:question}).locator('..');
 const card=()=>entry().locator('.msg.a[role=button]');
 const point=async(x,y)=>page.locator('.plan .host svg').evaluate((svg,[x,y])=>{const p=new DOMPoint(x,y).matrixTransform(svg.getScreenCTM());return {x:p.x,y:p.y};},[x,y]);
 const drag=async(a,b)=>{const p=await point(...a),q=await point(...b);await page.mouse.move(p.x,p.y);await page.mouse.down();await page.mouse.move(q.x,q.y,{steps:6});await page.mouse.up();};
 try{
  const ready=page.waitForResponse(r=>r.url().includes('/api/lens/status'));
  await page.goto('http://127.0.0.1:3011/drawing?id=cubi');await ready;
  await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByRole('textbox',{name:'Your question'}).fill(question);
  await page.locator('button[aria-label=Ask]').click();
  await page.locator('.review').waitFor();
  const proposal=page.locator('.review-layer rect[data-id="proposal-1"]');await proposal.waitFor();
  await drag([100,70],[115,70]);
  await page.getByRole('button',{name:'Draw',exact:true}).click();await drag([150,150],[210,210]);
  assert.equal(await page.locator('.review-layer rect').count(),2);
  await page.getByRole('button',{name:'Save labels',exact:true}).click();
  await card().getByRole('button',{name:/Edit labels/}).waitFor();
  assert((await card().innerText()).includes('I found 2 doors.'));
  assert.equal(await card().locator('.cites > div').count(),2);
  assert.equal(await card().locator('.cites > div').filter({hasText:'Door · human-verified label'}).count(),2);
  assert(!/Host-derived|Human corrections|Checked by you/.test(await card().innerText()));
  await card().getByRole('button',{name:/Edit labels/}).click();
  assert.equal(await page.locator('.review-layer rect').count(),2);
  assert.equal(Number(await proposal.getAttribute('width')),75);
  await proposal.click();
  await page.getByRole('button',{name:'Save labels',exact:true}).click();
  await card().getByRole('button',{name:/Edit labels/}).waitFor();
  assert((await card().innerText()).includes('I found 1 door.'));
  assert.equal(await card().locator('.cites > div').count(),1);
  // Drop all local conversation data: the API must restore the saved revision.
  await page.evaluate(()=>localStorage.removeItem('ymir.thread.d:cubi'));await page.reload();
  await card().getByRole('button',{name:/Edit labels/}).waitFor();
  assert((await card().innerText()).includes('I found 1 door.'));
  await card().getByRole('button',{name:/Edit labels/}).click();
  await page.locator('.trace-chart.sequential .tnode').first().waitFor();
  assert.equal(await page.locator('.tnode').filter({hasText:'Inspect'}).count(),2);
  assert.equal(await page.locator('.review-layer .rbox.off').count(),1);
  assert.equal(await page.locator('.review-layer .rbox.keep').count(),1);
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  assert((await card().innerText()).includes('I found 1 door.'));
  // A failed save keeps the draft editable and does not claim success.
  await card().getByRole('button',{name:/Edit labels/}).click();await proposal.click();
  await page.route('**/api/reviews',route=>route.fulfill({status:503,json:{detail:'Could not save labels'}}));
  await page.getByRole('button',{name:'Save labels',exact:true}).click();
  await page.locator('.toast').filter({hasText:'Your edits are still here'}).waitFor();
  assert.equal(await page.locator('.review-layer .rbox.keep').count(),2);
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  assert((await card().innerText()).includes('I found 1 door.'));
  await card().getByRole('button',{name:/Show source/}).first().click();
  assert.equal(await page.locator('.evidence-layer rect.evidence').count(),1);
  await page.screenshot({path:'/tmp/ymir-labels-edited.png',fullPage:true});
  const records=await page.evaluate(async()=>fetch('/api/reviews?drawing_id=cubi').then(r=>r.json()));
  const latest=records.at(-1);assert(latest.revision_of);assert.equal(latest.user_count,1);
  assert.equal(records.filter(r=>r.job_id===latest.job_id).length,2);
  assert.equal(dispatches,1);
  assert(!/Checked by you/.test(await page.locator('.trace').innerText()));
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/ymir-labels-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:['resize and add labels','edit accepted and rejected labels','single verified citation per object','reload from server without local thread','cancel retains saved labels','failed save retains draft','immutable revision history','one inference dispatch','2D evidence','mobile layout'],errors}));
 }catch(error){await page.screenshot({path:'/tmp/ymir-labels-failure.png',fullPage:true});throw error;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
