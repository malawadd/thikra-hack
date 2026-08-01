import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const child = spawn(process.platform === 'win32' ? 'uv.exe' : 'uv', ['run', 'uvicorn', 'app.main:app', '--reload', '--port', process.env.API_PORT || '43192'], {
  cwd: path.join(root, 'services', 'api'), stdio: 'inherit'
});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', (code) => process.exit(code ?? 0));
