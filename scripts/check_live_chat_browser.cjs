// Timed streaming and fresh server conversation checks; no paid inference.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const origin=process.env.YMIR_TEST_FRONTEND || 'http://127.0.0.1:3013';
const backend=process.env.YMIR_TEST_BACKEND || 'http://127.0.0.1:8012';
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],requests=[],jobLoads=[];
  const namespace='-live-chat-'+Date.now();
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url());let postData;
   if(url.pathname==='/api/ask' && req.method()==='POST'){
    const body=req.postDataJSON();requests.push(body);
    postData=JSON.stringify({...body,project_id:body.project_id+namespace,conversation_id:body.conversation_id+namespace});
    assert.equal(req.headers().accept,'text/event-stream');
   }
   if(url.pathname==='/api/lens/jobs'){
    jobLoads.push(url.searchParams.get('conversation_id'));
    for(const key of ['project_id','conversation_id'])url.searchParams.set(key,url.searchParams.get(key)+namespace);
   }
   await route.continue({url:backend+url.pathname+url.search,...(postData?{postData}:{})});
  });
  await page.goto(origin+'/building?id=ac20');await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByText('Smart ready',{exact:true}).waitFor();
  const clear=page.getByRole('button',{name:'Clear chat',exact:true}),send=page.getByRole('button',{name:'Ask',exact:true}),question=page.getByRole('textbox',{name:'Your question'});
  assert(await clear.isDisabled());const selected=await page.locator('.ask-scope input:checked').count();
  await question.fill('Count doors for live progress');
  const stream=page.waitForResponse(r=>r.url().endsWith('/api/ask') && r.request().method()==='POST');
  await send.click();const response=await stream;
  assert(response.headers()['content-type'].startsWith('text/event-stream'));
  await page.locator('.pending').filter({hasText:'Finding door'}).waitFor({timeout:15000});
  assert.equal(await page.locator('.msg.a:not(.pending)').count(),0,'Progress must render before any final answer');
  assert(await clear.isDisabled());await page.locator('.pending-elapsed').waitFor();
  await page.screenshot({path:'/tmp/ymir-live-progress-desktop.png',fullPage:true});
  await page.locator('.review').waitFor({timeout:20000});await page.getByRole('button',{name:'Skip',exact:true}).click();
  const data=(await response.text()).split('\n').filter(line=>line.startsWith('data: ')).map(line=>JSON.parse(line.slice(6)));
  const originalJob=data.find(e=>e.type==='job').job_id;
  await clear.click();assert.equal(await page.locator('.msg.q').count(),0);
  assert.equal(await page.locator('.ask-scope input:checked').count(),selected);
  assert.equal(await page.getByRole('combobox',{name:'Response mode'}).inputValue(),'smart');
  await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();await page.getByText('Smart ready',{exact:true}).waitFor();
  assert.equal(await page.locator('.msg.q').count(),0,'Reload must not restore cleared conversation');
  const fresh=jobLoads.at(-1);assert.notEqual(fresh,requests[0].conversation_id);
  await question.fill('Count doors after clearing');await send.click();
  assert.equal(requests.at(-1).conversation_id,fresh);assert.deepEqual(requests.at(-1).prior_job_ids,[]);
  await page.locator('.review').waitFor({timeout:20000});await page.getByRole('button',{name:'Skip',exact:true}).click();
  const retained=await fetch(backend+'/api/lens/jobs/'+originalJob).then(r=>r.json());assert.equal(retained.status,'completed');
  await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();await page.getByText('Smart ready',{exact:true}).waitFor();
  assert.equal(await page.locator('.msg.q').count(),1);assert((await page.locator('.msg.q').innerText()).includes('after clearing'));
  await page.setViewportSize({width:390,height:844});await clear.scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/ymir-clear-chat-mobile.png',fullPage:true});
  await clear.focus();await page.keyboard.press('Enter');assert.equal(await page.locator('.msg.q').count(),0);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:['SSE activity renders before final answer','real tool stage labels','elapsed time','clear disabled during active request','fresh server conversation after clear','no earlier job IDs','clear persists on reload','drawing scope retained','prior receipts preserved','keyboard clear','mobile bounds'],errors,requests:requests.length}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
