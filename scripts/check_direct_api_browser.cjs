// Production frontend, actual cross-origin API traffic; no request interception.
const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const pathLib=require('node:path');
const {spawnSync}=require('node:child_process');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(60000);
 const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests.push(r.url());});
 try{
  await page.goto('http://127.0.0.1:3013/home');
  const path=process.argv[2] || '/tmp/ymir-direct-upload.png';
  if(!fs.existsSync(path)){
   const generated=spawnSync(pathLib.join(__dirname,'../.venv/bin/python'),['-c',`from PIL import Image,PngImagePlugin
import sys
m=PngImagePlugin.PngInfo()
for i in range(9):m.add_text('qa-padding-'+str(i),'Drawing upload QA. '*35000)
Image.open(sys.argv[1]).save(sys.argv[2],pnginfo=m)`,pathLib.join(__dirname,'../data/samples/cubi.png'),path],{encoding:'utf8'});
   if(generated.status!==0)throw new Error(generated.stderr || 'Could not prepare the PNG fixture');
  }
  assert(fs.statSync(path).size>4.5*1024*1024);
  await page.locator('input[type=file]').first().setInputFiles(path);
  await page.waitForURL('**/drawing?id=*');
  await page.waitForFunction(()=>document.querySelector('.plan image')?.getAttribute('href')?.startsWith('http://127.0.0.1:8013/'));
  await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByRole('textbox',{name:'Your question'}).fill(`Count windows direct API QA ${Date.now()}`);
  await page.locator('button[aria-label=Ask]').click();
  await page.getByRole('button',{name:'Reconnect',exact:true}).waitFor();
  await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.locator('.review').waitFor();
  await page.getByRole('button',{name:'Save labels',exact:true}).click();
  await page.locator('.mark.ok').filter({hasText:'Labels saved'}).waitFor();
  await page.reload();await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.locator('.msg.a').filter({hasText:'I found 3 windows'}).waitFor();
  assert(requests.some(u=>u.includes('/api/documents')));assert(requests.some(u=>u.includes('/events')));
  assert(requests.every(u=>u.startsWith('http://127.0.0.1:8013/')));
  assert.deepEqual(errors,[]);await page.screenshot({path:'/tmp/ymir-direct-api.png',fullPage:true});
  console.log(JSON.stringify({checks:['production frontend','direct CORS upload above 4.5 MB','direct source image','NDJSON streaming','reload and reconnect','editable review persistence','no frontend API proxy'],bytes:fs.statSync(path).size,errors}));
 }catch(error){await page.screenshot({path:'/tmp/ymir-direct-api-failure.png',fullPage:true});throw error;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
