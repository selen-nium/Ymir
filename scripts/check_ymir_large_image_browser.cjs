// Run with the local path to a >30 MP PNG. All writes go to the no-inference QA backend.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const path=process.argv[2];if(!path)throw new Error('Pass the path to a large PNG.');
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];let asks=0;page.setDefaultTimeout(60000);
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{const u=new URL(route.request().url());if(u.pathname==='/api/ask'){asks++;return route.abort();}await route.continue({url:'http://127.0.0.1:8012'+u.pathname+u.search});});
 try{
  await page.goto('http://127.0.0.1:3011/home');
  await page.locator('input[type=file]').first().setInputFiles(path);
  await page.waitForURL('**/drawing?id=*');
  await page.locator('.plan .vnote').filter({hasText:'full-resolution original preserved'}).waitFor();
  const id=new URL(page.url()).searchParams.get('id');
  const drawing=await page.evaluate(async id=>fetch(`/api/drawings/${id}`).then(r=>r.json()),id);
  const reg=drawing.source.image_registration;
  assert(reg.resized);assert(reg.original_size[0]*reg.original_size[1]>30000000);
  assert(drawing.image.size[0]*drawing.image.size[1]<=30000000);
  const original=await page.evaluate(async id=>fetch(`/api/documents/${id}/original`).then(r=>r.arrayBuffer()).then(b=>b.byteLength),drawing.source.document_id);
  assert.equal(original,fs.statSync(path).size);
  assert.equal(asks,0);assert.deepEqual(errors,[]);
  await page.screenshot({path:'/tmp/ymir-large-image-upload.png',fullPage:true});
  console.log(JSON.stringify({checks:['browser upload of high-resolution PNG','automatic working-image resize','source coordinate registration','original bytes preserved','zero inference calls'],originalBytes:original,originalSize:reg.original_size,workingSize:drawing.image.size,errors}));
 }catch(error){await page.screenshot({path:'/tmp/ymir-large-image-failure.png',fullPage:true});throw error;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
