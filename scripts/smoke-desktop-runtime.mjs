import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('The packaged runtime smoke test is Windows-only.');
const root = path.resolve(import.meta.dirname, '..');
const runtime = path.join(root, 'apps', 'desktop', 'src-tauri', 'resources', 'runtime');
const api = path.join(runtime, 'api', 'thikra-api.exe');
const ffmpeg = path.join(runtime, 'ffmpeg');
const data = await mkdtemp(path.join(os.tmpdir(), 'thikra-packaged-smoke-'));
for (const folder of ['database', 'assets', 'proxies', 'cache', 'logs']) await mkdir(path.join(data, folder));

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

function environment(port) {
  return {
    SystemRoot: process.env.SystemRoot,
    WINDIR: process.env.WINDIR,
    LOCALAPPDATA: process.env.LOCALAPPDATA,
    APPDATA: process.env.APPDATA,
    PATH: ffmpeg,
    THIKRA_DESKTOP: '1',
    APP_MODE: 'DEMO',
    DATABASE_URL: `sqlite:///${path.join(data, 'database', 'thikra.db').replaceAll('\\', '/')}`,
    THIKRA_DATA_DIR: path.join(data, 'assets'),
    STEP_CACHE_DIR: path.join(data, 'cache'),
    THIKRA_LOG_PATH: path.join(data, 'logs', 'engine.jsonl'),
    THIKRA_FONT_DIR: path.join(runtime, 'fonts'),
    THIKRA_API_BASE_URL: `http://127.0.0.1:${port}`,
    API_CORS_ORIGINS: 'http://tauri.localhost,tauri://localhost',
    PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring',
  };
}

async function waitFor(url, attempts = 120) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try { const response = await fetch(url); if (response.ok) return response; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function runOnce(expectProject) {
  const port = await freePort();
  const child = spawn(api, ['--host', '127.0.0.1', '--port', String(port)], { cwd:data, env:environment(port), windowsHide:true, stdio:['ignore','pipe','pipe'] });
  const output = [];
  child.stdout.on('data', (chunk) => output.push(chunk.toString()));
  child.stderr.on('data', (chunk) => output.push(chunk.toString()));
  const base = `http://127.0.0.1:${port}`;
  try {
    await waitFor(`${base}/health/ready`);
    let projects = (await (await fetch(`${base}/studio/projects`)).json()).items;
    if (expectProject && projects.length !== 1) throw new Error('SQLite state did not survive packaged runtime restart.');
    if (!expectProject) {
      const created = await fetch(`${base}/studio/projects`, { method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({name:'Packaged smoke',description:'Offline DEMO verification',budget_cap_minor:500,currency:'USD'}) });
      if (!created.ok) throw new Error(`Could not create DEMO project: ${await created.text()}`);
      projects = [await created.json()];
    }
    const storage = await (await fetch(`${base}/studio/storage-connection`)).json();
    if (storage.mode !== 'local' || storage.configured) throw new Error('Packaged Studio did not default to local storage.');
  } catch (error) {
    console.error(output.join('').slice(-12_000));
    throw error;
  } finally {
    if (child.exitCode === null) {
      await new Promise((resolve) => {
        child.once('exit', resolve);
        child.kill();
      });
    }
  }
}

try {
  await runOnce(false);
  await runOnce(true);
  console.log('Frozen API smoke test passed with no Python, Node, uv, or system ffmpeg on PATH.');
} finally {
  await rm(data, { recursive:true, force:true });
}
