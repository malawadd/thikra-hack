import { createHash } from 'node:crypto';
import { createReadStream, existsSync } from 'node:fs';
import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const manifestPath = path.join(root, 'packaging', 'windows-runtime.json');
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
const cache = path.join(root, '.cache', 'desktop-runtime');
const resources = path.join(root, 'apps', 'desktop', 'src-tauri', 'resources', 'runtime');
const archive = path.join(cache, manifest.ffmpeg.archive);

if (process.platform !== 'win32') throw new Error('The v0.1.1 packaged runtime targets Windows x64 only.');
await mkdir(cache, { recursive: true });
await rm(resources, { recursive: true, force: true });
await mkdir(resources, { recursive: true });
await writeFile(path.join(resources, '.gitkeep'), '');

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', shell: false, ...options });
  if (result.status !== 0) throw new Error(`${command} failed with exit code ${result.status}`);
}

async function sha256(file) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

if (!existsSync(archive) || await sha256(archive) !== manifest.ffmpeg.sha256) {
  await rm(archive, { force: true });
  const response = await fetch(manifest.ffmpeg.url, { redirect: 'follow' });
  if (!response.ok || !response.body) throw new Error(`Could not download pinned ffmpeg: HTTP ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  await writeFile(archive, bytes);
}
const actual = await sha256(archive);
if (actual !== manifest.ffmpeg.sha256) throw new Error(`ffmpeg checksum mismatch: expected ${manifest.ffmpeg.sha256}, received ${actual}`);

const extracted = path.join(cache, `ffmpeg-${manifest.ffmpeg.version}`);
await rm(extracted, { recursive: true, force: true });
await mkdir(extracted, { recursive: true });
run(
  'powershell.exe',
  ['-NoProfile', '-Command', 'Expand-Archive -LiteralPath $env:THIKRA_FFMPEG_ARCHIVE -DestinationPath $env:THIKRA_FFMPEG_EXTRACTED -Force'],
  { env: { ...process.env, THIKRA_FFMPEG_ARCHIVE: archive, THIKRA_FFMPEG_EXTRACTED: extracted } },
);
const extractedItems = await readdir(extracted);
if (extractedItems.length !== 1) throw new Error('Pinned ffmpeg archive has an unexpected layout.');
const ffmpegRoot = path.join(extracted, extractedItems[0]);
const mediaDir = path.join(resources, 'ffmpeg');
await mkdir(mediaDir, { recursive: true });
for (const binary of ['ffmpeg.exe', 'ffprobe.exe']) await cp(path.join(ffmpegRoot, 'bin', binary), path.join(mediaDir, binary));
for (const notice of ['LICENSE.txt', 'README.txt']) {
  const source = path.join(ffmpegRoot, notice);
  if (existsSync(source)) await cp(source, path.join(mediaDir, notice));
}
await cp(manifestPath, path.join(mediaDir, 'windows-runtime.json'));
await cp(path.join(root, 'packaging', 'THIRD_PARTY_NOTICES.md'), path.join(mediaDir, 'THIRD_PARTY_NOTICES.md'));

const fontDir = path.join(resources, 'fonts');
await mkdir(fontDir, { recursive: true });
for (const font of manifest.fonts) {
  const destination = path.join(fontDir, font.name);
  const response = await fetch(font.url);
  if (!response.ok) throw new Error(`Could not download ${font.name}: HTTP ${response.status}`);
  await writeFile(destination, Buffer.from(await response.arrayBuffer()));
  if (await sha256(destination) !== font.sha256) throw new Error(`${font.name} checksum mismatch`);
}
const fontLicense = await fetch(manifest.font_license.url);
if (!fontLicense.ok) throw new Error(`Could not download font license: HTTP ${fontLicense.status}`);
const fontLicensePath = path.join(fontDir, 'OFL.txt');
await writeFile(fontLicensePath, Buffer.from(await fontLicense.arrayBuffer()));
if (await sha256(fontLicensePath) !== manifest.font_license.sha256) throw new Error('Font license checksum mismatch');

run('uv', ['sync', '--project', 'services/api', '--group', 'desktop', '--frozen']);
run('uv', [
  'run', '--project', 'services/api', '--group', 'desktop', 'pyinstaller',
  '--clean', '--noconfirm', '--distpath', 'services/api/dist', '--workpath', 'services/api/build/desktop',
  'services/api/desktop.spec',
]);
await cp(path.join(root, 'services', 'api', 'dist', 'thikra-api'), path.join(resources, 'api'), { recursive: true });

const ffmpegExe = path.join(mediaDir, 'ffmpeg.exe');
const probe = spawnSync(ffmpegExe, ['-hide_banner', '-buildconf'], { encoding: 'utf8' });
const config = `${probe.stdout}\n${probe.stderr}`;
for (const flag of manifest.ffmpeg.required_configuration) {
  if (!config.includes(flag)) throw new Error(`Pinned ffmpeg is missing required configuration ${flag}`);
}
for (const required of [path.join(resources, 'api', 'thikra-api.exe'), ffmpegExe, path.join(mediaDir, 'ffprobe.exe'), path.join(mediaDir, 'LICENSE.txt'), path.join(fontDir, 'NotoSansArabic.ttf'), fontLicensePath]) {
  if (!existsSync(required)) throw new Error(`Packaged runtime is missing ${required}`);
}
console.log(`Packaged Thikra runtime at ${resources}`);
