import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { ThikraClient } from '@thikra/sdk';
import OpenAI from 'openai';

const apiUrl = (process.env.THIKRA_API_URL ?? 'http://127.0.0.1:43192').replace(/\/$/, '');
const apiKey = process.env.THIKRA_API_KEY ?? 'thikra_test_demo_local_only';
const approveDemo = process.argv.includes('--approve-demo');
const useOpenAI = process.argv.includes('--openai');
const budgetMinor = 1000;

const baseRequest = process.env.THIKRA_BUYER_REQUEST ?? 'Buy a verified 15-second vertical Arabic advertisement for Noura Glow. Target young adults in Saudi Arabia. Show the product clearly. Do not use a real person or celebrity likeness. Do not invent medical claims. Require a verification report and provenance manifest.';

function trace(step: string, detail: string) { console.log(`[buyer-agent] ${step.padEnd(13)} ${detail}`); }

async function interpretedBrief(): Promise<string> {
  if (!useOpenAI) return baseRequest;
  if (!process.env.OPENAI_API_KEY) throw new Error('--openai requires OPENAI_API_KEY');
  trace('openai', 'Interpreting creative intent; payment credentials and budget are not in the prompt.');
  const response = await new OpenAI().responses.create({
    model: process.env.OPENAI_MODEL ?? 'gpt-5.5',
    input: `Normalize this buyer request into one concise creative production brief. Preserve every restriction exactly. Do not add claims, people, providers, pricing, payment instructions, or relaxed constraints.\n\n${baseRequest}`
  });
  return response.output_text.trim();
}

async function discoverWithMcp() {
  const client = new Client({ name: 'thikra-external-buyer-demo', version: '1.0.0' });
  const transport = new StreamableHTTPClientTransport(new URL(`${apiUrl}/mcp`), {
    requestInit: { headers: { Authorization: `Bearer ${apiKey}` } }
  });
  await client.connect(transport);
  const tools = await client.listTools();
  const result = await client.callTool({ name: 'thikra_list_services', arguments: {} });
  const catalog = result.structuredContent as { items: Array<{ slug: string; maximum_price_minor: number }> };
  await client.close();
  trace('mcp', `${tools.tools.length} tools; ${catalog.items.length} active services discovered.`);
  const service = catalog.items.find((item) => item.slug === 'verified-vertical-ad');
  if (!service) throw new Error('Flagship verified-vertical-ad service is unavailable');
  if (service.maximum_price_minor > budgetMinor) throw new Error('Published service ceiling exceeds the fixed buyer budget');
  return service;
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.entries(value as Record<string, unknown>).sort(([a],[b]) => a.localeCompare(b)).map(([key,item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`;
  return JSON.stringify(value);
}

function decodeBase64Url(value: string): ArrayBuffer {
  const normalized = value.replaceAll('-', '+').replaceAll('_', '/').padEnd(Math.ceil(value.length / 4) * 4, '=');
  const bytes = Buffer.from(normalized, 'base64');
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

async function verifyReceiptLocally(receipt: Record<string, any>): Promise<boolean> {
  const keyDocument = await fetch(`${apiUrl}/.well-known/thikra-signing-keys.json`).then((response) => response.json()) as { keys: Array<{kid:string;x:string}> };
  const published = keyDocument.keys.find((key) => key.kid === receipt.signing_key_id);
  if (!published) return false;
  const digest = Buffer.from(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(receipt.receipt_payload)))).toString('hex');
  if (digest !== receipt.receipt_hash) return false;
  const key = await crypto.subtle.importKey('raw', decodeBase64Url(published.x), { name: 'Ed25519' }, false, ['verify']);
  return crypto.subtle.verify('Ed25519', key, decodeBase64Url(receipt.signature), new TextEncoder().encode(canonical(receipt.receipt_payload)));
}

async function main() {
  trace('mode', useOpenAI ? 'OpenAI-assisted intent normalization' : 'Deterministic scripted mode');
  await discoverWithMcp();
  const brief = await interpretedBrief();
  const thikra = new ThikraClient({ apiKey, baseUrl: apiUrl });
  const quote = await thikra.quotes.create({
    service: 'verified-vertical-ad',
    input: { brief, language: 'ar', durationSeconds: 15, aspectRatio: '9:16', resolution: '1080x1920', requiredElements: ['Noura Glow product clearly visible', 'Arabic narration'], forbiddenElements: ['real person', 'celebrity likeness', 'medical claims'], claimConstraints: ['No unsupported medical or treatment claims'], humanReviewRequired: true, maximumRetries: 2 },
    buyerPrincipal: { type: 'HUMAN', displayName: 'Noura Glow demo buyer', email: 'buyer@nouraglow.sa', externalReference: 'noura-demo-principal' },
    buyerAgent: { name: 'External Noura buyer agent', developerName: 'Thikra demo integrator', framework: 'MCP + @thikra/sdk', modelName: useOpenAI ? (process.env.OPENAI_MODEL ?? 'gpt-5.5') : 'deterministic-script', externalAgentId: 'noura-external-agent-demo' },
    maximumBudget: { amountMinor: budgetMinor, currency: 'USD' }
  });
  if (quote.total_minor > budgetMinor) throw new Error(`Quote ${quote.total_minor} exceeds immutable budget ${budgetMinor}`);
  trace('quote', `${quote.id} · ${quote.total_minor} ${quote.currency} minor units · expires ${quote.expires_at}`);
  await thikra.quotes.accept(quote.id);
  const order = await thikra.orders.create({ quoteId: quote.id, externalReference: `agent-demo-${Date.now()}` });
  trace('order', `${order.public_order_number} · ${order.status} · unpaid`);
  const authorization = await thikra.payments.authorize(order.id, 'noura-demo-buyer', 'buyer@nouraglow.sa');
  trace('authorize', `${authorization.checkout.approval_url} · expires ${authorization.checkout.expires_at}`);
  if (!approveDemo) {
    trace('approval', 'Human approval required. Re-run with --approve-demo only for the clearly simulated DEMO gateway.');
    return;
  }
  if (!authorization.payment.simulated) throw new Error('--approve-demo is forbidden for non-demo payment authorizations');
  trace('approval', 'Explicit --approve-demo flag received; confirming simulated payment only.');
  await thikra.payments.confirmDemo(order.id, 'noura-demo-buyer');
  let state = await thikra.orders.start(order.id);
  trace('fulfillment', `${state.status} · existing mandate/run pipeline linked`);
  if (state.status === 'REVIEW_REQUIRED') {
    trace('verification', 'Controlled demo fixture requested one policy-bounded retry.');
    state = await thikra.orders.retry(order.id, 'Arabic narration', 'Controlled deterministic verification failure');
    trace('retry', `${state.status} · replacement passed re-verification`);
  }
  if (state.status !== 'DELIVERED') throw new Error(`Expected verified delivery, received ${state.status}`);
  const delivery = await thikra.deliverables.list(order.id);
  const receipt = await thikra.deliverables.receipt(order.id);
  const remoteVerification = await thikra.deliverables.verifyReceipt(receipt);
  const localVerification = await verifyReceiptLocally(receipt);
  if (!remoteVerification.valid || !localVerification) throw new Error('Delivery receipt signature verification failed');
  const media = delivery.deliverables.find((item) => item.type === 'final') ?? delivery.deliverables[0];
  const response = await fetch(media.download_url);
  if (!response.ok) throw new Error(`Deliverable download failed (${response.status})`);
  const outputDirectory = resolve(process.cwd(), 'artifacts');
  await mkdir(outputDirectory, { recursive: true });
  const outputPath = resolve(outputDirectory, String(media.name));
  await writeFile(outputPath, Buffer.from(await response.arrayBuffer()));
  trace('receipt', `${receipt.receipt_hash} · Ed25519 verified remotely and locally`);
  trace('complete', `${order.public_order_number} · saved ${outputPath}`);
}

main().catch((error) => { console.error('[buyer-agent] FAILED', error instanceof Error ? error.message : error); process.exitCode = 1; });
