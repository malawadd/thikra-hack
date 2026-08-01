import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { tmpdir } from 'node:os';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const databasePath = path.join(tmpdir(), `thikra-e2e-${process.pid}.db`).replaceAll('\\', '/');
const e2eEnv = { ...process.env, APP_MODE: 'DEMO', DATABASE_URL: `sqlite:///${databasePath}`, THIKRA_DATA_DIR: path.join(tmpdir(), `thikra-e2e-evidence-${process.pid}`), THIKRA_API_BASE_URL: 'http://127.0.0.1:43292', PUBLIC_WEB_URL: 'http://127.0.0.1:43191', ORIGIN: 'http://127.0.0.1:43191', THIKRA_DEMO_API_KEY: 'thikra_test_demo_local_only' };
const api = spawn(process.platform === 'win32' ? 'uv.exe' : 'uv', ['run', 'uvicorn', 'app.main:app', '--port', '43292'], {
  cwd: path.join(root, 'services', 'api'), stdio: 'inherit', env: e2eEnv
});

let apiReady = false;
for (let attempt = 0; attempt < 120; attempt += 1) {
  try {
    const response = await fetch('http://127.0.0.1:43292/health/ready');
    if (response.ok) { apiReady = true; break; }
  } catch {
    // The API process is still starting.
  }
  await new Promise((resolve) => setTimeout(resolve, 250));
}
if (!apiReady) {
  api.kill();
  throw new Error('Thikra API did not become ready before the E2E timeout.');
}

const web = spawn(process.execPath, [path.join(root, 'apps', 'web', 'build', 'index.js')], {
  cwd: path.join(root, 'apps', 'web'), stdio: 'inherit',
  env: { ...e2eEnv, API_INTERNAL_URL: 'http://127.0.0.1:43292', HOST: '127.0.0.1', PORT: '43191' }
});
const stopChild = (child) => {
  if (!child.pid) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
  } else {
    child.kill();
  }
};
const stop = () => { stopChild(api); stopChild(web); };
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, stop);
api.on('exit', (code) => { if (code) { web.kill(); process.exit(code); } });
web.on('exit', (code) => { if (code) { api.kill(); process.exit(code); } });
