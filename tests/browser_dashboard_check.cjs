// Standalone Chromium dashboard checks. Synthetic data only; no cloud account.
// PLAYWRIGHT_BROWSERS_PATH="$PWD/.build/browsers" node tests/browser_dashboard_check.cjs
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(path.resolve('.build/browser-check/node_modules/playwright'));
const metric = (value, quality='valid') => ({value,quality,updated_at:'2026-11-18T04:17:03Z'});
const status = {
  phase:'collecting',connection:'online',reason:'configured',region:'cn',model:'A1 mini',printer_id:'SYNTHETIC-P1',
  configured:true,persistent:false,setup_stage:'home',devices:[],operation:null,
  config:{liveness_seconds:60,initial_delay_seconds:1,max_delay_seconds:60,jitter:false},
  metrics:{remaining_minutes:metric(0,'stale'),progress_percent:metric(0),current_layer:metric(0),total_layers:metric(0),nozzle_celsius:metric(21.28),bed_celsius:metric(21.47),chamber_celsius:metric(null,'unsupported'),part_fan_percent:metric(0),aux_fan_percent:metric(null,'unsupported')},
  ams_materials:[{id:'0',trays:[{id:'3',material:metric('PETG','stale'),color:metric('FF000080','stale')},{id:'0',material:metric('<img src=x onerror=alert(1)>'),color:metric('22AA44FF')},{id:'2',material:metric(''),color:metric('red; background:url(https://invalid/)')}]}],
  diagnostics:{generation:3,reason:'target_telemetry'},
  telemetry:{latest:{received_at:'2026-11-18T04:17:03Z',generation:3,data:{print:{message:'<script>alert(1)</script>',nozzle_temper:21.28}}},fields:[{path:'print.nozzle_temper',value:21.28,updated_at:'2026-11-18T04:17:03Z',generation:3},{path:'print.history',value:'<img src=x onerror=alert(1)>',updated_at:'2026-11-18T04:08:31Z',generation:2}],truncated:true}
};
let reads = 0, writes = 0;
const server = http.createServer((req,res) => {
  res.setHeader('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'");
  if (req.url === '/api/status') {reads++;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(status));return;}
  if (req.method !== 'GET') {writes++;res.writeHead(400);res.end();return;}
  const asset = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[req.url];
  if (!asset) {res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',asset.endsWith('.js')?'application/javascript':asset.endsWith('.css')?'text/css':'text/html');
  res.end(fs.readFileSync(path.resolve('src/bambu_monitor/webui',asset)));
});
(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
    browser = await chromium.launch({headless:true});
    const page = await browser.newPage({viewport:{width:1100,height:1000}});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error') errors.push(m.text());});
    await page.goto(`http://127.0.0.1:${server.address().port}/#SYNTHETIC-LOCAL-SESSION`);
    await page.waitForFunction(()=>document.querySelectorAll('.metric-card').length===9);
    assert.match(await page.locator('#metric-summary').innerText(),/6 项有效 · 1 项旧值 · 2 项不支持/);
    assert.match(await page.locator('.metric-card').first().innerText(),/0 分钟[\s\S]*旧值/);
    assert.equal(await page.locator('.tray-card').count(),4);
    assert.match(await page.locator('.tray-card').nth(1).innerText(),/ID 1[\s\S]*未知/);
    assert.match(await page.locator('.tray-card').nth(3).innerText(),/ID 3[\s\S]*PETG[\s\S]*旧值/);
    assert.equal(await page.locator('.tray-card').nth(0).locator('.color-swatch').evaluate(el=>el.style.backgroundColor),'rgb(34, 170, 68)');
    assert.equal(await page.locator('.tray-card').nth(2).locator('.color-swatch').evaluate(el=>el.style.backgroundColor),'');
    assert.equal(await page.locator('#ams-materials img, #raw-telemetry script, #telemetry-fields img').count(),0);
    assert.match(await page.locator('#raw-telemetry').textContent(),/<script>alert/);
    await page.locator('#fields-details summary').click();
    await page.locator('#raw-details summary').click();
    assert.equal(await page.locator('#refresh-readings').isEnabled(),true);
    await page.locator('#refresh-readings').click();
    await page.waitForTimeout(1200);
    assert.equal(await page.locator('#fields-details').evaluate(el=>el.open),true);
    assert.equal(await page.locator('#raw-details').evaluate(el=>el.open),true);
    assert.equal(writes,0);
    assert.ok(reads>=3);
    status.phase='idle';status.connection=null;status.setup_stage='login';
    await page.locator('#refresh-readings').click();
    await page.locator('#account').fill('synthetic@example.invalid');
    await page.locator('#credential').fill('SYNTHETIC-ONLY');
    await page.waitForTimeout(1200);
    assert.equal(await page.locator('#account').inputValue(),'synthetic@example.invalid');
    assert.equal(await page.locator('#credential').inputValue(),'SYNTHETIC-ONLY');
    status.metrics={};status.ams_materials=[];status.telemetry={latest:null,fields:[],truncated:false};status.diagnostics=null;
    await page.locator('#refresh-readings').click();
    await page.waitForFunction(()=>document.getElementById('metric-summary').textContent.includes('9 项未知'));
    assert.equal(await page.locator('.metric-card').count(),9);
    assert.match(await page.locator('#metric-summary').innerText(),/9 项未知/);
    assert.equal(await page.locator('#ams-materials .tray-card').count(),0);
    assert.match(await page.locator('#raw-telemetry').innerText(),/尚未收到/);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({result:'passed',input_class:'synthetic',checks:['nine metrics and quality/time','valid zero','AMS slots by ID','missing material','stale material','validated RGBA swatch','XSS text safety','details survive polling','read-only refresh while collecting','refresh sends no writes','wizard input retained','stopped/empty state','mobile width','CSP and JS errors']}));
  } finally {if(browser) await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
