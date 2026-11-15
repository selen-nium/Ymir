// Window editing and animation checks use only the no-inference backend on :8012.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(20000);
 const errors=[];let dispatches=0;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{const u=new URL(route.request().url());if(u.pathname==='/api/ask')dispatches++;await route.continue({url:'http://127.0.0.1:8012'+u.pathname+u.search});});
 const question=`Count windows animation QA ${Date.now()}`;
 const card=()=>page.locator('.msg.q').filter({hasText:question}).locator('..').locator('.msg.a[role=button]');
 const canvas=()=>page.locator('.model canvas');
 const settle=async count=>{await page.waitForFunction(n=>{const c=document.querySelector('.model canvas');return c?.dataset.windowCount===String(n) && c.dataset.windowMotion==='settled';},count);};
 try{
  const ready=page.waitForResponse(r=>r.url().includes('/api/lens/status'));
  await page.goto('http://127.0.0.1:3011/drawing?id=cubi');await ready;
  await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByRole('textbox',{name:'Your question'}).fill(question);await page.locator('button[aria-label=Ask]').click();
  await page.locator('.review').waitFor();await settle(3);
  await page.locator('.model .vnote').filter({hasText:'Preview · 3 windows'}).waitFor();
  await page.getByRole('button',{name:'Save labels',exact:true}).click();
  await page.locator('.model canvas[data-window-motion=running]').waitFor();
  await page.screenshot({path:'/tmp/ymir-windows-confirming.png',fullPage:true});
  await settle(3);await card().getByRole('button',{name:/Edit labels/}).waitFor();
  await page.locator('.model .vnote').filter({hasText:'3 verified windows · height estimated'}).waitFor();
  await page.screenshot({path:'/tmp/ymir-windows-verified.png',fullPage:true});
  await card().getByRole('button',{name:/Edit labels/}).click();
  await page.locator('.review-layer rect[data-id="window-2"]').click();await settle(2);
  assert((await page.locator('.model .vnote').innerText()).includes('Preview · 2 windows'));
  await page.getByRole('button',{name:'Cancel',exact:true}).click();await settle(3);
  await card().getByRole('button',{name:/Edit labels/}).click();
  await page.locator('.review-layer rect[data-id="window-2"]').click();
  await page.getByRole('button',{name:'Save labels',exact:true}).click();await settle(2);
  await card().getByRole('button',{name:/Edit labels/}).waitFor();
  await page.getByRole('tab',{name:'Build',exact:true}).click();await settle(2);
  assert(!/Claude|RapidOCR/i.test(await page.locator('body').innerText()));
  await page.evaluate(()=>localStorage.removeItem('ymir.thread.d:cubi'));await page.reload();await settle(2);
  await page.getByRole('tab',{name:'Build',exact:true}).click();
  await page.locator('.model .vnote').filter({hasText:'2 verified windows'}).waitFor();
  assert(!/Claude|RapidOCR/i.test(await page.locator('body').innerText()));
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.getByRole('tab',{name:'Ask',exact:true}).click();await card().getByRole('button',{name:/Edit labels/}).click();
  await page.locator('.review-layer rect[data-id="window-2"]').click();await settle(3);
  await page.getByRole('button',{name:'Save labels',exact:true}).click();
  await card().getByRole('button',{name:/Edit labels/}).waitFor();await settle(3);
  assert.equal(await canvas().getAttribute('data-window-motion'),'settled');
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>{const c=document.querySelector('.model canvas'),r=c?.getBoundingClientRect();return r && Math.abs(c.width-r.width*Math.min(devicePixelRatio,2))<2;});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/ymir-windows-mobile.png',fullPage:true});
  assert.equal(dispatches,1);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:['three window shapes with live preview','confirmation animation','remove and cancel','saved edit changes physical windows','Build and fresh-browser persistence','retired Build steps hidden','reduced motion','mobile layout','one mock inference dispatch'],errors}));
 }catch(error){await page.screenshot({path:'/tmp/ymir-windows-failure.png',fullPage:true});throw error;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
