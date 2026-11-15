// Deterministic UI checks against the no-inference browser fixture on :8012.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{const u=new URL(route.request().url());await route.continue({url:'http://127.0.0.1:8012'+u.pathname+u.search});});
  await page.goto('http://127.0.0.1:3011/building?id=ac20');
  await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByText('Ymir ready',{exact:false}).waitFor();
  await page.getByRole('textbox',{name:'Your question'}).fill('Count doors for trace QA');
  await page.locator('button[aria-label="Ask"]').click();
  const dfine=page.locator('.tnode.run').filter({hasText:'D-FINE-L'});
  await dfine.waitFor({timeout:15000});assert((await dfine.innerText()).includes('door'));
  // Browsing earlier steps should not be undone as new work arrives.
  await page.locator('.chartwrap').evaluate(el=>{el.scrollLeft=0;});
  await page.locator('.tnode.run').filter({hasText:'Kimi'}).waitFor({timeout:15000});
  assert.equal(await page.locator('.chartwrap').evaluate(el=>el.scrollLeft),0);
  await page.locator('.review .warn').filter({hasText:'Check the boxes'}).waitFor();
  const nodes=await page.locator('.trace-chart.sequential .tnode').evaluateAll(ns=>ns.map(n=>({id:n.dataset.id,left:parseFloat(n.style.left),width:n.getBoundingClientRect().width,text:n.innerText,step:n.dataset.step})));
  assert.equal(new Set(nodes.map(n=>n.id)).size,nodes.length);
  assert.equal(nodes.filter(n=>n.text.includes('Ymir response')).length,3);
  assert.equal(nodes.filter(n=>n.text.includes('Inspect')).length,2);
  assert(nodes.some(n=>n.text.includes('D-FINE-L')) && nodes.some(n=>n.text.includes('Kimi')));
  for(let i=1;i<nodes.length;i++)assert(nodes[i].left>=nodes[i-1].left+nodes[i-1].width);
  assert.equal(await page.locator('.trace-chart .tnode.run').count(),0);
  assert.equal(await page.locator('.trace-chart .tnode.you').count(),1);
  await page.locator('.chartwrap').evaluate(el=>{el.scrollLeft=el.scrollWidth;});
  await page.screenshot({path:'/tmp/ymir-trace-sequential.png',fullPage:true});
  await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();
  const reopen=page.locator('.msg.a[role=button]').last();await reopen.click();
  if(await page.getByRole('button',{name:'Expand the trace'}).isVisible())await page.getByRole('button',{name:'Expand the trace'}).click();
  await page.locator('.trace-chart.sequential .tnode').first().waitFor();
  assert.equal(await page.locator('.tnode').filter({hasText:'Inspect'}).count(),2);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator('.chartwrap').evaluate(el=>{el.scrollLeft=el.scrollWidth;});
  assert(await page.locator('.chartwrap').evaluate(el=>el.scrollLeft>0));
  assert.deepEqual(errors,[]);
  // A historical production receipt still renders as a chain without rerunning it.
  const recorded=fs.readFileSync('var/live-checks/aecv-doors.events.jsonl','utf8').trim().split('\n').map(JSON.parse);
  const history=await browser.newPage({viewport:{width:1440,height:1000}});
  await history.addInitScript(events=>localStorage.setItem('ymir.thread.ac20',JSON.stringify([{id:'saved',q:'Recorded trace',sheet:'ac20-eg',status:'done',answer:{status:'answered',text:'Recorded trace playback'},events,at:Date.now()}])),recorded);
  await history.route('**/api/lens/jobs?*',route=>route.fulfill({json:[]}));
  await history.goto('http://127.0.0.1:3011/building?id=ac20');
  await history.locator('.msg.a').filter({hasText:'Recorded trace playback'}).click();
  if(await history.getByRole('button',{name:'Expand the trace'}).isVisible())await history.getByRole('button',{name:'Expand the trace'}).click();
  await history.locator('.trace-chart.sequential .tnode').first().waitFor();
  const expected=recorded.filter(e=>e.type==='progress' && e.activity.phase==='started').length+1;
  assert.equal(await history.locator('.tnode').count(),expected);
  assert(await history.locator('.tnode').filter({hasText:'D-FINE-L'}).count()>0);
  console.log(JSON.stringify({checks:['execution order','repeated inspections','running D-FINE-L and Kimi labels','no overlapping nodes','manual scroll preserved','review chaining','reload replay','mobile overflow','historical production replay without inference'],nodes:nodes.length,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
