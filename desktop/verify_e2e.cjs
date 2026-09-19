const { chromium } = require('/home/whu/.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright');

(async () => {
  const pid = require('fs').readFileSync('/tmp/pettwin-e2e/pet_id.txt', 'utf8').trim();
  const browser = await chromium.launch({
    executablePath: '/home/whu/.cache/ms-playwright/chromium-1234/chrome-linux/chrome',
    args: ['--no-sandbox', '--use-gl=angle', '--enable-webgl'],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(`http://127.0.0.1:8798/index.html?pet=${pid}&api=http://127.0.0.1:8797&name=%E6%A9%98%E5%AD%90`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(9000); // 等 glb 加载 + 首次轮询

  const dbg = await page.evaluate(async () => window.__twin ? {
    hasCat: window.__twin.hasCat,
    furCount: window.__twin.furCount,
    actions: window.__twin.actions,
    curAnim: window.__twin.curAnim,
    apiLive: window.__twin.apiLive,
    style: window.__twin.style,
    target: window.__twin.target,
  } : null);

  const pill = await page.textContent('#statePill');
  const name = await page.textContent('#petName');
  const bars = await page.evaluate(() => Object.fromEntries(['energy','gait','tail','mood'].map(k => [k, document.getElementById('v_'+k).textContent])));
  await page.screenshot({ path: '/tmp/pettwin-e2e/shot2.png' });

  console.log(JSON.stringify({ dbg, pill, name, bars, errors }, null, 1));
  await browser.close();

  // 断言
  const assert = (c, msg) => { if (!c) { console.error('ASSERT FAIL: ' + msg); process.exitCode = 1; } };
  assert(dbg, '__twin 未注册(脚本崩了)');
  if (dbg) {
    assert(dbg.hasCat, '模型未加载');
    assert(dbg.actions.length === 12, '动画数=' + dbg.actions.length);
    assert(dbg.furCount === 5, '毛发壳=' + dbg.furCount);
    assert(dbg.apiLive, 'API 未连通');
    assert(dbg.target.energy < 0.5, 'energy 应低(蔫): ' + dbg.target.energy);
    assert(dbg.curAnim === 'Eating', '动画应 Eating: ' + dbg.curAnim);
  }
  assert(!errors.length, '页面错误: ' + errors.join(' | '));
})();
