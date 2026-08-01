import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const api = spawn(process.platform === 'win32' ? 'uv.exe' : 'uv', ['run', 'uvicorn', 'app.main:app', '--port', '43292'], {
  cwd: path.join(root, 'services', 'api'), stdio: 'inherit'
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

const web = spawn(process.execPath, [path.join(root, 'apps', 'web', 'node_modules', 'vite', 'bin', 'vite.js'), 'dev', '--host', '127.0.0.1', '--port', '43191'], {
  cwd: path.join(root, 'apps', 'web'), stdio: 'inherit',
  env: { ...process.env, API_INTERNAL_URL: 'http://127.0.0.1:43292' }
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
