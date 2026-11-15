// Read-only UI check: source formats, sheet switching, and responsive labels.
const {chromium,request}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const backend=process.env.YMIR_TEST_API || 'https://bersamin12--ymir-web.us-east.modal.run';
const frontend=process.env.YMIR_TEST_FRONTEND || 'http://127.0.0.1:3011';
(async()=>{
 const api=await request.newContext({baseURL:backend});
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(30000);
 const errors=[];let writes=0,override=null;page.on('pageerror',e=>errors.push(e.message));
 const project={id:'file-types',title:'File type UI fixture',levels:[{id:'L0',name:'Ground floor',z_m:0,height_m:3}],sheets:[
  {drawing_id:'sample-cubicasa-2536',views:[{kind:'floor_plan',kindSure:true,discipline:'architecture',placement:{type:'plan',level:'L0',transform:[.05,0,0,0],by:'user'}}]},
  {drawing_id:'sample-swiss-5733',views:[{kind:'floor_plan',kindSure:true,discipline:'architecture',placement:{type:'attached',by:'user'}}]},
 ]};
 await page.route('**/api/**',async route=>{
  const req=route.request(),u=new URL(req.url());
  if(!['GET','HEAD'].includes(req.method())){writes++;return route.abort();}
  if(u.pathname==='/api/projects/file-types')return route.fulfill({json:project});
  const response=await api.fetch(u.pathname+u.search,{method:req.method()});
  if(u.pathname==='/api/drawings/sample-cubicasa-2536' && override!==null){const data=await response.json();data.source.file=override;return route.fulfill({json:data});}
  await route.fulfill({response});
 });
 const badge=()=>page.locator('.panel .source-file-type');
 const expectType=async type=>{await page.waitForFunction(type=>document.querySelector('.panel .source-file-type')?.textContent===`File type: ${type}`,type);assert.equal(await badge().count(),1);};
 try{
  await page.goto(frontend+'/drawing?id=sample-cubicasa-2536');await expectType('PNG');
  await page.goto(frontend+'/drawing?id=sample-swiss-5733');await expectType('PDF');
  assert((await badge().getAttribute('title')).endsWith('.pdf'));
  await page.getByRole('tab',{name:'Build',exact:true}).click();await expectType('PDF');
  await page.getByRole('tab',{name:'Ask',exact:true}).click();await expectType('PDF');
  await page.goto(frontend+'/building?id=junction-ac20');await expectType('IFC');
  await page.screenshot({path:'/tmp/ymir-file-type-building.png',fullPage:true});
  await page.goto(frontend+'/building?id=file-types');await expectType('PNG');
  await page.locator('.rail .trow .pick').filter({hasText:'Swiss'}).click();await expectType('PDF');
  await page.locator('.rail .trow .pick').filter({hasText:'CubiCasa'}).click();await expectType('PNG');
  const ghost=page.locator('.rail .trow.ghost .pick').first();await ghost.click();assert.equal(await badge().count(),0);
  override='rooms.svg';await page.goto(frontend+'/drawing?id=sample-cubicasa-2536');await expectType('SVG');
  override='Scan.JPEG';await page.reload();await expectType('JPEG');
  override='source-without-extension';await page.reload();await page.locator('.panel .src .src-title').waitFor();assert.equal(await badge().count(),0);
  override=null;await page.goto(frontend+'/drawing?id=sample-swiss-5733');await expectType('PDF');
  await page.setViewportSize({width:390,height:844});await badge().scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert(await badge().evaluate(el=>{const a=el.getBoundingClientRect(),b=el.closest('.panel').getBoundingClientRect();return a.left>=b.left && a.right<=b.right;}));
  await page.screenshot({path:'/tmp/ymir-file-type-mobile.png',fullPage:true});
  assert.equal(writes,0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:['original PDF format despite PNG preview','PNG and IFC','format changes with selected sheet','Build and Ask headers','SVG and uppercase JPEG metadata','no guessed type for unknown sources or missing sheets','mobile layout'],writes,errors}));
 }catch(e){await page.screenshot({path:'/tmp/ymir-file-type-failure.png',fullPage:true});throw e;}
 finally{await page.unrouteAll({behavior:'wait'});await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exit(1)});
