import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const windows = process.platform === 'win32';
function run(command, args, shell = false) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', shell });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
if (process.env.npm_execpath) {
  run(process.execPath, [process.env.npm_execpath, 'install']);
} else {
  run(windows ? 'pnpm.cmd' : 'pnpm', ['install'], windows);
}
run(process.execPath, [path.join(root, 'scripts', 'setup-api.mjs')]);
