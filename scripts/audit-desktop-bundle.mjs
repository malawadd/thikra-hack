import { createHash } from 'node:crypto';
import { createReadStream, existsSync } from 'node:fs';
import { cp, mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const runtime = path.join(root, 'apps', 'desktop', 'src-tauri', 'resources', 'runtime');
const bundle = path.join(root, 'apps', 'desktop', 'src-tauri', 'target', 'release', 'bundle');
const artifacts = path.join(root, 'artifacts', 'desktop-release');
const desktopVersion = JSON.parse(await readFile(path.join(root, 'apps', 'desktop', 'package.json'), 'utf8')).version;
const required = [
  'api/thikra-api.exe',
  'api/_internal/migrations/env.py',
  'ffmpeg/ffmpeg.exe',
  'ffmpeg/ffprobe.exe',
  'ffmpeg/LICENSE.txt',
  'ffmpeg/THIRD_PARTY_NOTICES.md',
  'fonts/NotoSans.ttf',
  'fonts/NotoSansArabic.ttf',
  'fonts/NotoSerif.ttf',
  'fonts/OFL.txt',
];
for (const item of required) {
  if (!existsSync(path.join(runtime, ...item.split('/')))) throw new Error(`Runtime audit failed: missing ${item}`);
}

async function walk(directory) {
  const result = [];
  for (const name of await readdir(directory)) {
    const item = path.join(directory, name);
    if ((await stat(item)).isDirectory()) result.push(...await walk(item)); else result.push(item);
  }
  return result;
}

for (const file of await walk(runtime)) {
  const name = path.basename(file).toLowerCase();
  if (name === '.env' || name.endsWith('.key') || name === 'credentials' || name.startsWith('id_rsa')) {
    throw new Error(`Runtime audit failed: secret-like file ${file}`);
  }
  if (name.endsWith('.pem') && /PRIVATE KEY/.test(await readFile(file, 'utf8'))) {
    throw new Error(`Runtime audit failed: private key material in ${file}`);
  }
}

const installers = (await walk(bundle)).filter(
  (file) => (/\.msi$|setup\.exe$/i.test(file)) && path.basename(file).includes(`_${desktopVersion}_`),
);
if (!installers.some((file) => file.endsWith('.msi')) || !installers.some((file) => /setup\.exe$/i.test(file))) {
  throw new Error('Bundle audit failed: both MSI and NSIS setup EXE are required.');
}
await rm(artifacts, { recursive: true, force: true });
await mkdir(artifacts, { recursive: true });

async function sha256(file) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}
const sums = [];
for (const installer of installers) {
  const destination = path.join(artifacts, path.basename(installer));
  await cp(installer, destination);
  sums.push(`${await sha256(destination)}  ${path.basename(destination)}`);
}
await writeFile(path.join(artifacts, 'SHA256SUMS.txt'), `${sums.sort().join('\n')}\n`);
console.log(`Audited ${installers.length} installers and wrote ${artifacts}`);
