import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
if (!args.length) throw new Error('An API command is required.');
const result = spawnSync(process.platform === 'win32' ? 'uv.exe' : 'uv', ['run', ...args], {
  cwd: path.join(root, 'services', 'api'), stdio: 'inherit'
});
process.exit(result.status ?? 1);
