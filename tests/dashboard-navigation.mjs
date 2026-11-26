import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile, unlink } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

const [browser, work, port] = process.argv.slice(2);
const child = spawn(browser, ['--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
  '--remote-debugging-port=0', '--user-data-dir=' + work + '/navigation', 'about:blank'], {stdio:'ignore'});
const exit = once(child, 'exit');
let socket;
async function waitFor(check) {
  for (let attempt = 0; attempt < 100; attempt++) {
    try { if (await check()) return; } catch {}
    await delay(70);
  }
  throw new Error('Dashboard navigation timed out');
}
try {
  let debugPort;
  await waitFor(async () => {
    debugPort = (await readFile(work + '/navigation/DevToolsActivePort', 'utf8')).split('\n')[0];
    return debugPort;
  });
  const targets = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json();
  socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await once(socket, 'open');
  const pending = new Map();
  let next = 1;
  socket.addEventListener('message', ({data}) => {
    const message = JSON.parse(data);
    const resolve = pending.get(message.id);
    if (resolve) resolve(message);
  });
  async function call(method, params) {
    const id = next++;
    const result = new Promise(resolve => pending.set(id, resolve));
    socket.send(JSON.stringify({id, method, params}));
    const message = await result;
    pending.delete(id);
    assert.ifError(message.error);
    return message.result;
  }
  async function evaluate(expression) {
    const {result, exceptionDetails} = await call('Runtime.evaluate', {expression, returnByValue:true});
    assert.ifError(exceptionDetails);
    return result.value;
  }
  async function enter(selector) {
    assert.equal(await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus();
      document.activeElement.matches(${JSON.stringify(selector)})`), true);
    const key = {key:'Enter', code:'Enter', windowsVirtualKeyCode:13, nativeVirtualKeyCode:13};
    await call('Input.dispatchKeyEvent', {type:'keyDown', text:'\r', ...key});
    await call('Input.dispatchKeyEvent', {type:'keyUp', ...key});
  }
  const url = 'http://127.0.0.1:' + port + '/';
  for (const width of [1280, 375]) {
    await call('Emulation.setDeviceMetricsOverride', {width, height:900, deviceScaleFactor:1, mobile:width < 400});
    await call('Page.navigate', {url});
    await waitFor(() => evaluate("Boolean(document.querySelector('.graph-edge'))"));
    assert.equal(await evaluate("document.querySelectorAll('.graph-edge').length"), 1);
    assert.equal(await evaluate("document.querySelector('[aria-label=\"Fixture Analyst can assign to Fixture QA\"]').getAttribute('href')"),
      '#/connections/analyst/qa');
    await enter('.graph-node[href="#/agents/qa"]');
    await waitFor(() => evaluate("!document.getElementById('persona-view').hidden"));
    await enter('#persona-view a');
    await waitFor(() => evaluate("!document.getElementById('overview').hidden"));
    if (width < 400) {
      await enter('#connection-list summary');
      await waitFor(() => evaluate("document.getElementById('connection-list').open"));
      await enter('#connection-list a[href="#/connections/analyst/qa"]');
    } else await enter('.graph-edge');
    await waitFor(() => evaluate("!document.getElementById('connection-details').hidden"));
    assert.equal(await evaluate("document.activeElement.textContent"), 'Fixture Analyst → Fixture QA');
    const detail = await evaluate("document.getElementById('connection-details').textContent");
    assert.match(detail, /Returned/);
    assert.match(detail, /Task outcome · Verified/);
    assert.match(detail, /Older unclassified/);
    assert.equal(await evaluate("document.querySelectorAll('#connection-details a[href*=\"directed-thread\"]').length"), 1);
    assert.equal(await evaluate("document.querySelectorAll('#connection-details a[href*=\"reverse-thread\"]').length"), 0);
    await enter('#connection-details a[href="#connections-heading"]');
  }
  await unlink(work + '/connections.json');
  await call('Page.navigate', {url:url + '?fixture=offline'});
  await waitFor(() => evaluate("Boolean(document.querySelector('.graph-edge'))"));
  await enter('.graph-edge');
  await waitFor(() => evaluate("document.getElementById('connection-details').textContent.includes('history is unavailable')"));
  console.log('Keyboard and mobile navigation verified for graph arrows, agent profiles and directional evidence.');
} finally {
  socket?.close();
  child.kill();
  await exit;
}
