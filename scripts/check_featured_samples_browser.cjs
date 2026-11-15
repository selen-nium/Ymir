// Curated examples must open from cache without uploads, builds or paid inference.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(30000);
 const errors=[];let writes=0;page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{const u=new URL(route.request().url());if(route.request().method==='POST'){writes++;return route.abort();}await route.continue({url:'http://127.0.0.1:8012'+u.pathname+u.search});});
 try{
  await page.goto('http://127.0.0.1:3011/home');
  for(const id of ['sample-cubicasa-2536','sample-swiss-5733'])await page.locator(`a[href="/drawing?id=${id}"]`).waitFor();
  const projects=['junction-ac20','junction-duplex','junction-digitalhub','junction-sports-complex'];
  for(const id of projects)await page.locator(`a[href="/building?id=${id}"]`).waitFor();
  assert.equal(await page.locator('a[href="/drawing?id=finna"],a[href="/building?id=ac20"]').count(),0);
  for(const img of await page.locator('a[href*=junction-duplex] img').all()){
   assert(await img.evaluate(el=>el.getBoundingClientRect().height<=el.parentElement.getBoundingClientRect().height+1));
  }
  await page.getByText('Sample sources and credits',{exact:true}).click();
  assert((await page.locator('body').innerText()).includes('OpeningDesign'));
  await page.screenshot({path:'/tmp/ymir-featured-home.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/ymir-featured-home-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});
  for(const [kind,id] of [...projects.map(id=>['building',id]),['drawing','sample-cubicasa-2536'],['drawing','sample-swiss-5733']]){
   const ready=page.waitForResponse(r=>r.url().includes('/api/lens/status'));
   await page.goto(`http://127.0.0.1:3011/${kind}?id=${id}`);await ready;
   await page.locator('.model canvas').waitFor();
   await page.waitForFunction(()=>{const img=document.querySelector('.plan image');return img && img.getAttribute('href');});
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert(!/Claude vision|RapidOCR/.test(await page.locator('body').innerText()));
   assert.equal(await page.locator('.model canvas').evaluate(c=>c.toDataURL().length>5000),true);
   await page.screenshot({path:`/tmp/ymir-featured-${id}.png`,fullPage:true});
  }
  assert.equal(writes,0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:['six curated examples','all requested dataset families','source credits','legacy examples omitted from Home','each cached 2D/3D preview loads','mobile layout','zero build or inference requests'],writes,errors}));
 }catch(error){await page.screenshot({path:'/tmp/ymir-featured-failure.png',fullPage:true});throw error;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
