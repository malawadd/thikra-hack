import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const api = path.join(root, 'services', 'api');
const windows = process.platform === 'win32';
const uv = windows ? 'uv.exe' : 'uv';
const venv = path.join(api, '.venv');
function run(args) {
  const result = spawnSync(uv, args, { cwd: root, stdio: 'inherit' });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
const python = path.join(venv, windows ? 'Scripts' : 'bin', windows ? 'python.exe' : 'python');
if (!existsSync(python)) run(['venv', venv]);
run(['pip', 'install', '--python', venv, '-r', path.join(api, 'requirements.txt')]);
