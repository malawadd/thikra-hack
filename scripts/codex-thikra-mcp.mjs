import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const action = process.argv[2] || 'connect';
const managedStart = '# BEGIN THIKRA CODEX MCP';
const managedEnd = '# END THIKRA CODEX MCP';
const configPath = path.join(process.env.CODEX_HOME || path.join(os.homedir(), '.codex'), 'config.toml');
const block = `${managedStart}\n[mcp_servers.thikra]\nurl = "http://localhost:43192/mcp/"\nbearer_token_env_var = "THIKRA_API_KEY"\ndefault_tools_approval_mode = "writes"\n${managedEnd}\n`;

function usage() {
  console.log('Usage: pnpm codex:connect | codex:update | codex:disconnect');
}

function promptSecret(label) {
  if (!process.stdin.isTTY || !process.stdin.setRawMode) {
    throw new Error('Run this command in an interactive terminal so the API key can be entered safely.');
  }
  process.stdout.write(label);
  return new Promise((resolve, reject) => {
    let value = '';
    const finish = () => {
      process.stdin.setRawMode(false);
      process.stdin.pause();
      process.stdin.off('data', onData);
      process.stdout.write('\n');
      resolve(value.trim());
    };
    const onData = (chunk) => {
      const character = chunk.toString('utf8');
      if (character === '\u0003') {
        process.stdin.setRawMode(false);
        process.stdin.pause();
        process.stdin.off('data', onData);
        reject(new Error('Cancelled.'));
      } else if (character === '\r' || character === '\n') {
        finish();
      } else if (character === '\u007f' || character === '\b') {
        if (value) {
          value = value.slice(0, -1);
          process.stdout.write('\b \b');
        }
      } else if (!character.startsWith('\u001b')) {
        value += character;
        process.stdout.write('*');
      }
    };
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.on('data', onData);
  });
}

export function removeThikraServer(source) {
  let result = source.replace(/^# BEGIN THIKRA CODEX MCP\r?\n[\s\S]*?^# END THIKRA CODEX MCP\r?\n?/gm, '');
  const header = /^\[mcp_servers\.thikra\]\s*\r?$/m.exec(result);
  if (!header || header.index === undefined) return result.trimEnd();
  const afterHeader = header.index + header[0].length;
  const nextSection = result.slice(afterHeader).search(/^\[/m);
  result = `${result.slice(0, header.index)}${nextSection < 0 ? '' : result.slice(afterHeader + nextSection)}`;
  return result.trimEnd();
}

async function updateConfig(includeThikra) {
  const existing = existsSync(configPath) ? await readFile(configPath, 'utf8') : '';
  const withoutThikra = removeThikraServer(existing);
  const next = includeThikra ? `${withoutThikra}${withoutThikra ? '\n\n' : ''}${block}` : `${withoutThikra}${withoutThikra ? '\n' : ''}`;
  await mkdir(path.dirname(configPath), { recursive: true });
  await writeFile(configPath, next, 'utf8');
}

function setWindowsUserKey(value) {
  if (process.platform !== 'win32') {
    throw new Error('This bootstrap currently supports Codex Desktop on Windows only.');
  }
  const result = spawnSync('setx.exe', ['THIKRA_API_KEY', value], { stdio: 'ignore' });
  if (result.status !== 0) throw new Error('Could not save THIKRA_API_KEY as a Windows User environment variable.');
}

function deleteWindowsUserKey() {
  if (process.platform !== 'win32') return;
  const result = spawnSync('reg.exe', ['delete', 'HKCU\\Environment', '/v', 'THIKRA_API_KEY', '/f'], { stdio: 'ignore' });
  if (result.status !== 0 && result.status !== 1) {
    throw new Error('Could not remove THIKRA_API_KEY from the Windows User environment.');
  }
}

async function main() {
  if (!['connect', 'update', 'disconnect'].includes(action)) {
    usage();
    return 1;
  } else if (action === 'disconnect') {
    await updateConfig(false);
    deleteWindowsUserKey();
    console.log('Thikra has been disconnected from Codex. Fully quit and reopen Codex Desktop.');
  } else {
    const key = await promptSecret('Paste the Thikra API key (input is hidden): ');
    if (!key.startsWith('thikra_')) throw new Error('That does not look like a Thikra API key. Nothing was changed.');
    setWindowsUserKey(key);
    await updateConfig(true);
    console.log(`Thikra MCP ${action === 'connect' ? 'connected' : 'updated'} at ${configPath}.`);
    console.log('Fully quit and reopen Codex Desktop, then use /mcp to confirm the connection.');
  }
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    process.exitCode = await main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
