import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const activeRoots = [path.join(root, 'apps', 'web', 'src'), path.join(root, 'apps', 'desktop', 'src')];
const extensions = new Set(['.ts', '.js', '.svelte']);
const files = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(target);
    else if (extensions.has(path.extname(entry.name))) files.push(target);
  }
}
for (const active of activeRoots) if (fs.existsSync(active)) walk(active);
const rules = [
  { label: 'React/Next import', pattern: /(?:from\s+['"](?:react|next(?:\/|['"]))|require\(['"](?:react|next))/ },
  { label: 'provider SDK in browser', pattern: /from\s+['"](?:openai|replicate|runwayml|@google\/genai)/ },
  { label: 'direct B2/AWS SDK in frontend', pattern: /from\s+['"](?:@aws-sdk|boto3|backblaze-b2)/ },
  { label: 'private secret name in browser source', pattern: /(?:PRAVA_SECRET_KEY|MERCHANT_SECRET_KEY|B2_APPLICATION_KEY|OPENAI_API_KEY)/ },
  { label: 'unfinished product placeholder', pattern: /\b(?:T[O]DO|F[I]XME|Coming s[o]on)\b/ }
];
const offenders = [];
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  for (const rule of rules) if (rule.pattern.test(source)) offenders.push(`${path.relative(root, file)}: ${rule.label}`);
}
if (offenders.length) {
  console.error(offenders.join('\n'));
  process.exit(1);
}
console.log(`Active SvelteKit web + desktop structure verified (${files.length} source files).`);
