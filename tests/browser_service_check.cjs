// Optional real Chromium UI acceptance. Uses only synthetic cloud transports.
// npm install --prefix .build/browser-check --cache .build/npm-cache playwright
// PLAYWRIGHT_BROWSERS_PATH="$PWD/.build/browsers" node tests/browser_service_check.cjs
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require(path.resolve('.build/browser-check/node_modules/playwright'));

(async () => {
  const child = spawn(process.env.BAMBU_TEST_PYTHON || '.venv-dev/bin/python', ['tests/browser_service_fixture.py'], {stdio:['ignore','pipe','pipe']});
  let browser;
  const errors = [];
  try {
    const url = await new Promise((resolve,reject) => {
      let text='';
      const timeout=setTimeout(()=>reject(Error('fixture startup timeout')),10000);
      child.stdout.on('data', chunk => {
        text += chunk.toString();
        if(text.includes('\n')) {clearTimeout(timeout);resolve(JSON.parse(text.split('\n')[0]).url);}
      });
      child.on('exit',code=>{clearTimeout(timeout);reject(Error('fixture exited '+code));});
    });
    browser = await chromium.launch({headless:true});
    const context = await browser.newContext({viewport:{width:1100,height:1000}});
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => {if(m.type()==='error' && m.text().includes('Content Security Policy')) errors.push(m.text());});
    await page.goto(url);
    await page.locator('[data-action="region-cn"]').click();
    await page.locator('#account').fill('synthetic@example.invalid');
    await page.locator('#credential').fill('SYNTHETIC-PRIVATE-9981');
    // Let two status polls pass: they must not erase private fields or form choices.
    await page.waitForTimeout(2200);
    assert.equal(await page.locator('#account').inputValue(),'synthetic@example.invalid');
    assert.equal(await page.locator('#credential').inputValue(),'SYNTHETIC-PRIVATE-9981');
    await page.evaluate(()=>{const p=document.createElement('p');p.id='synthetic-banner';p.textContent='合成离线预览 · 非真实云账号/遥测';document.querySelector('header').after(p);});
    await page.screenshot({path:'.build/service-login.png',fullPage:true});
    await page.locator('[data-action="login"]').click();
    await page.locator('#device').waitFor();
    assert.equal(await page.locator('#device').inputValue(),'');
    await page.locator('#device').selectOption('SYNTHETIC-P1');
    await page.locator('[data-action="select"]').click();
    await page.locator('#persistent').uncheck();
    await page.locator('[data-action="confirm"]').click();
    await page.locator('#settings:not([hidden])').waitFor();
    await page.locator('#live').fill('91');
    await page.waitForTimeout(2200);
    assert.equal(await page.locator('#live').inputValue(),'91');
    await page.locator('[data-action="save-settings"]').click();
    await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('保存'));
    await page.locator('#start').click();
    await page.waitForFunction(()=>document.getElementById('telemetry').textContent==='已收到目标遥测');
    await page.waitForFunction(()=>document.getElementById('metrics').textContent.includes('喷嘴 0°C'));
    await page.screenshot({path:'.build/service-dashboard.png',fullPage:true});
    await page.close();
    const reopened = await context.newPage();
    reopened.on('pageerror',e=>errors.push(e.message));
    await reopened.goto(url);
    await reopened.waitForFunction(()=>document.getElementById('telemetry').textContent==='已收到目标遥测');
    assert.equal(await reopened.locator('#live').inputValue(),'91');
    await reopened.locator('#stop').click();
    await reopened.waitForFunction(()=>document.getElementById('phase').textContent==='待命');
    assert.equal(await reopened.locator('#metrics').textContent(),'');
    await reopened.locator('[data-action="change-account"]').click();
    await reopened.locator('[data-action="region-global"]').click();
    await reopened.locator('[data-action="cancel"]').click();
    await reopened.locator('[data-action="logout"]').waitFor();
    assert.equal(await reopened.locator('#region-label').textContent(),'中国大陆');
    reopened.once('dialog',dialog=>dialog.accept());
    await reopened.locator('[data-action="logout"]').click();
    await reopened.locator('[data-action="region-cn"]').waitFor();
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({browser:'Chromium',input_class:'synthetic',result:'passed',checks:['full setup','explicit device choice','input retained across polls','settings persisted','valid zero','browser close preserves collector','stop invalidates telemetry','draft cancel preserves old region','logout','CSP and JS errors'],screenshots:['.build/service-login.png','.build/service-dashboard.png']}));
  } finally {
    if(browser) await browser.close();
    child.kill('SIGTERM');
    if(child.exitCode===null) await once(child,'exit');
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
