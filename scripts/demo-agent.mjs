import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const scratch = await mkdtemp(path.join(tmpdir(), 'thikra-agent-demo-'));
const databasePath = path.join(scratch, 'demo.db').replaceAll('\\', '/');
const port = await new Promise((resolve, reject) => {
  const server = createServer();
  server.once('error', reject);
  server.listen(0, '127.0.0.1', () => {
    const address = server.address();
    if (!address || typeof address === 'string') return reject(new Error('Could not allocate demo port'));
    server.close(() => resolve(address.port));
  });
});
const demoUrl = `http://127.0.0.1:${port}`;
const env = { ...process.env, APP_MODE: 'DEMO', DATABASE_URL: `sqlite:///${databasePath}`, THIKRA_DATA_DIR: path.join(scratch, 'evidence'), THIKRA_DEMO_API_KEY: 'thikra_test_demo_local_only', THIKRA_API_URL: demoUrl, THIKRA_API_BASE_URL: demoUrl };
const executable = process.platform === 'win32' ? path.join(root, 'services', 'api', '.venv', 'Scripts', 'python.exe') : path.join(root, 'services', 'api', '.venv', 'bin', 'python');
const api = spawn(executable, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)], { cwd: path.join(root, 'services', 'api'), env, stdio: ['ignore', 'pipe', 'pipe'] });
api.stdout.on('data', (value) => process.stdout.write(`[api] ${value}`));
api.stderr.on('data', (value) => process.stderr.write(`[api] ${value}`));

async function ready() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { if ((await fetch(`${demoUrl}/health/ready`)).ok) return; } catch { /* booting */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('Thikra API did not become ready');
}

try {
  await ready();
  const windows = process.platform === 'win32';
  const command = windows ? (process.env.ComSpec || 'cmd.exe') : 'pnpm';
  const args = windows ? ['/d', '/s', '/c', 'pnpm --filter @thikra/agent-client demo'] : ['--filter', '@thikra/agent-client', 'demo'];
  const agent = spawn(command, args, { cwd: root, env, stdio: 'inherit' });
  const code = await new Promise((resolve, reject) => { agent.once('error', reject); agent.once('exit', (value) => resolve(value ?? 1)); });
  if (code !== 0) process.exitCode = code;
} finally {
  api.kill('SIGTERM');
  if (api.exitCode === null) await new Promise((resolve) => api.once('exit', resolve));
  await rm(scratch, { recursive: true, force: true, maxRetries: 10, retryDelay: 150 });
}
