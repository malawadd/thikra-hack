import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const api = path.join(root, 'services', 'api');
const uv = process.platform === 'win32' ? 'uv.exe' : 'uv';
const migration = spawnSync(uv, ['run', 'alembic', 'upgrade', 'head'], {
  cwd: api, stdio: 'inherit'
});
if (migration.status !== 0) process.exit(migration.status ?? 1);
const child = spawn(uv, ['run', 'uvicorn', 'app.main:app', '--reload', '--port', process.env.API_PORT || '43192'], {
  cwd: api, stdio: 'inherit'
});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', (code) => process.exit(code ?? 0));
