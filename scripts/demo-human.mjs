import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const windows = process.platform === 'win32';
const command = windows ? (process.env.ComSpec || 'cmd.exe') : 'pnpm';
const args = windows ? ['/d', '/s', '/c', 'pnpm dev'] : ['dev'];
console.log('Starting Thikra Studio in DEMO mode at http://localhost:43191/services');
console.log('Payment and generation are simulated and visibly labeled. Press Ctrl+C to stop.');
const child = spawn(command, args, { cwd: root, env: { ...process.env, APP_MODE: 'DEMO', THIKRA_DEMO_API_KEY: 'thikra_test_demo_local_only' }, stdio: 'inherit' });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', (code) => { process.exitCode = code ?? 0; });
