import { spawn, spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

if (process.platform !== 'win32') throw new Error('The packaged Studio smoke test is Windows-only.');
for (const command of ['tauri-driver', 'msedgedriver']) {
  if (spawnSync('where.exe', [command], { stdio: 'ignore' }).status !== 0) {
    throw new Error(`${command} is required on PATH. See https://v2.tauri.app/develop/tests/webdriver/`);
  }
}

const root = resolve(import.meta.dirname, '..');
const application = resolve(root, 'apps/desktop/src-tauri/target/release/thikra-studio.exe');
const driver = spawn('tauri-driver', ['--port', '4444'], { cwd: root, stdio: 'ignore' });
let sessionId;

async function waitFor(url, attempts = 80) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try { const response = await fetch(url); if (response.ok) return; } catch {}
    await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

try {
  await waitFor('http://127.0.0.1:4444/status');
  const created = await fetch('http://127.0.0.1:4444/session', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ capabilities: { alwaysMatch: { 'tauri:options': { application } } } }),
  });
  if (!created.ok) throw new Error(`tauri-driver session failed: ${await created.text()}`);
  sessionId = (await created.json()).value.sessionId;
  await new Promise((resolveWait) => setTimeout(resolveWait, 1200));
  const titleResponse = await fetch(`http://127.0.0.1:4444/session/${sessionId}/title`);
  const title = (await titleResponse.json()).value;
  if (title !== 'Thikra Studio') throw new Error(`Unexpected desktop title: ${title}`);
  console.log('Thikra Studio packaged WebDriver smoke test passed.');
} finally {
  if (sessionId) await fetch(`http://127.0.0.1:4444/session/${sessionId}`, { method: 'DELETE' }).catch(() => {});
  driver.kill();
}
