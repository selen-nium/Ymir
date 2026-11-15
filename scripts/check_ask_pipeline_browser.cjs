// Real app and service/harness with no-charge model fixtures. See docs/ask-trace-pipeline.md.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const output='/tmp/ymir-ask-pipeline';
const frontend=process.env.YMIR_BROWSER_URL || 'http://127.0.0.1:3011';
fs.mkdirSync(output,{recursive:true});

(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:2800,height:1100}}),errors=[],requests=[];
  await page.emulateMedia({reducedMotion:'reduce'});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().includes('/api/lens/jobs/'))requests.push(r.url());});
  // Keep this check isolated even when .env.local points at a deployed API.
  await page.route('**/api/**',async route=>{const u=new URL(route.request().url());await route.continue({url:'http://127.0.0.1:8012'+u.pathname+u.search});});
  await page.goto(`${frontend}/building?id=ac20`);
  await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByText('Ymir ready',{exact:false}).waitFor();
  async function openTrace(){
   const expand=page.getByRole('button',{name:'Expand the trace'});
   if(await expand.isVisible())await expand.click();
  }
  async function send(q){
   await page.getByRole('textbox',{name:'Your question'}).fill(q);
   await page.locator('button[aria-label="Ask"]').click();
   await openTrace();
  }
  async function completed(){await page.locator('.tnode[data-id="stage-checks"]').waitFor({timeout:25000});}
  const chart=async()=>{
   // Opening the dock after replay schedules layout through ResizeObserver.
   await page.waitForFunction(()=>{
    const ns=[...document.querySelectorAll('.trace-chart .tnode')];
    return ns.length>0 && ns.every(n=>n.style.left!=='');
   });
   return page.locator('.trace-chart .tnode').evaluateAll(ns=>ns.map(n=>({
    id:n.dataset.id,name:n.querySelector('b').textContent,summary:n.querySelector('.tn-o').textContent,
    state:n.className,title:n.title,left:parseFloat(n.style.left),step:n.dataset.step})));
  };
  async function shot(name){
   await page.locator('.chartwrap').evaluate(el=>{el.scrollLeft=0;});
   await page.screenshot({path:`${output}/${name}.png`,fullPage:true});
  }
  await openTrace();
  const preview=await chart();
  assert.deepEqual(preview.map(n=>n.name),['Question','Queue','Route','Ymir responses','Tools','Host checks','Answer','Check boxes','Review result']);
  assert(preview.every(n=>n.state.includes('idle')));
  await shot('idle-preview');

  await send('Count doors');await completed();
  const compact=await chart();
  assert.deepEqual(compact.map(n=>n.name),['Question','Queue','Route','Ymir response 1','Detect objects','Ymir response 2','Host checks','Answer']);
  assert.equal(compact[2].summary,'Compact counter · door');
  assert.equal(compact[6].summary,'1 accepted · 0 errors · answered');
  assert(compact[4].title.includes('D-FINE-L'));
  await shot('compact-counter');

  await send('What does this sheet show?');await completed();
  const general=await chart();
  assert.equal(general[2].summary,'General harness');
  assert(general.some(n=>n.name==='Read pdf text'));
  await shot('general-harness');

  await send('Count windows');await completed();
  await page.locator('.review').waitFor();
  const review=await chart();
  assert.equal(review.find(n=>n.id==='stage-checks').summary,'1 accepted · 1 error · needs_review');
  assert.deepEqual(review.slice(-3).map(n=>n.name),['Evidence needs review','Check boxes','Review result']);
  assert(review.find(n=>n.id==='stage-checks').title.includes('Edge/clipped proposal'));
  await shot('needs-review');
  await page.getByRole('button',{name:'Save labels',exact:true}).click();
  await page.locator('.mark.ok').filter({hasText:'Labels saved'}).waitFor();
  assert((await chart()).at(-1).summary.includes('human-verified'));
  await shot('review-result');

  const firstAnswer=page.locator('.msg.a[role="button"]').filter({hasText:'I found 1 door.'}).first();
  await firstAnswer.click({position:{x:20,y:12}});
  await page.waitForFunction(()=>document.querySelector('.tnode[data-id="stage-route"] .tn-o')?.textContent==='Compact counter · door');
  assert.deepEqual(await chart(),compact);
  await shot('earlier-answer');
  // Discard the locally cached receipt, then prove the server artifact draws
  // the same chart. This edits only this test browser's localStorage.
  await page.evaluate(()=>{
   const key='ymir.thread.ac20',entries=JSON.parse(localStorage.getItem(key));
   entries.find(e=>e.q==='Count doors').events=[];localStorage.setItem(key,JSON.stringify(entries));
  });
  await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.locator('.msg.a[role="button"]').filter({hasText:'I found 1 door.'}).first().click({position:{x:20,y:12}});await openTrace();
  await page.locator('.tnode[data-id="stage-checks"]').waitFor();
  assert.deepEqual(await chart(),compact);
  assert(requests.some(u=>u.includes('/artifacts/events')));

  await page.request.post('http://127.0.0.1:8012/fixture/hold-next');
  await send('Count doors');
  await page.locator('.tnode.run[data-id="response-1"]').waitFor();
  const prefix=await chart();
  const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('ymir.thread.ac20')).at(-1));
  await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByRole('button',{name:'Reconnect',exact:true}).waitFor();await openTrace();
  await page.locator('.tnode.run[data-id="response-1"]').waitFor();
  assert.deepEqual(await chart(),prefix);
  assert(requests.some(u=>u.includes(`/api/lens/jobs/${stored.job_id}/events?after=`) && Number(new URL(u).searchParams.get('after'))>0));
  await shot('mid-job-reconnect');
  await page.request.post('http://127.0.0.1:8012/fixture/release');
  await completed();assert.deepEqual(await chart(),compact);
  await shot('reconnected-answer');
  const receipts=await page.request.get(`http://127.0.0.1:8012/api/lens/jobs/${stored.job_id}/artifacts/events`);
  const events=await receipts.json();
  assert.deepEqual(events.filter(e=>e.type==='stage').map(e=>e.stage),['question','queue','route','checks']);
  assert.equal(events.at(-1).type,'answer');
  const after=stored.events.at(-1).seq;
  const resumed=await page.request.get(`http://127.0.0.1:8012/api/lens/jobs/${stored.job_id}/events?after=${after}`);
  assert.deepEqual(stored.events.concat((await resumed.text()).trim().split('\n').map(JSON.parse)),events);
  assert.equal(new Set((await chart()).map(n=>n.id)).size,compact.length);
  const budget=await (await page.request.get('http://127.0.0.1:8012/api/lens/status')).json();
  assert.equal(budget.budget.actual_usd,0);assert.equal(budget.budget.unresolved_reserved_usd,0);
  for(const [i,n] of compact.entries())if(i)assert(n.left>=compact[i-1].left+172);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  const report={checks:['idle pending pipeline','compact route','general route','real host validation','box review saved','earlier-answer local replay','artifact replay','mid-job reconnect','NDJSON and artifact equality','no duplicate nodes','no overlap','mobile overflow','zero inference charges'],errors,job:stored.job_id,after,screenshots:fs.readdirSync(output).filter(n=>n.endsWith('.png'))};
  fs.writeFileSync(`${output}/report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
