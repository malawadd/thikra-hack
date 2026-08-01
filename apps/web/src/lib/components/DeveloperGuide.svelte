<script lang="ts">
  import Check from 'lucide-svelte/icons/check';
  import Copy from 'lucide-svelte/icons/copy';
  import ExternalLink from 'lucide-svelte/icons/external-link';
  import PageHeader from './PageHeader.svelte';
  let { topic = 'overview' }: { topic?: string } = $props();
  let copied = $state('');
  const apiBase = 'http://localhost:43192';
  const snippets: Record<string, string> = {
    curl: `curl -H "Authorization: Bearer $THIKRA_API_KEY" ${apiBase}/api/v1/services`,
    typescript: `const client = new ThikraClient({\n  apiKey: process.env.THIKRA_API_KEY!,\n  baseUrl: '${apiBase}'\n});\nconst services = await client.services.list();`,
    python: `import httpx, os\nservices = httpx.get(\n    '${apiBase}/api/v1/services',\n    headers={'Authorization': f'Bearer {os.environ["THIKRA_API_KEY"]}'}\n).json()`,
    mcp: `{"mcpServers":{"thikra":{"url":"${apiBase}/mcp","headers":{"Authorization":"Bearer ` + '${THIKRA_API_KEY}' + `"}}}}`
  };
  async function copy(name: string, value: string) { await navigator.clipboard.writeText(value); copied = name; setTimeout(() => copied = '', 1500); }
</script>

<PageHeader eyebrow="Agent Gateway" title="One commerce contract. REST and MCP." description="External agents use scoped API keys to discover services, obtain deterministic quotes, create orders, request human-approved payment authorization, and retrieve verified delivery evidence." />

<div class="developer-layout">
  <nav class="card developer-nav" aria-label="Developer documentation">
    <a class:active={topic === 'overview'} href="/developers">Overview</a>
    <a class:active={topic === 'mcp'} href="/developers/mcp">MCP</a>
    <a class:active={topic === 'rest'} href="/developers/rest">REST & OpenAPI</a>
    <a class:active={topic === 'webhooks'} href="/developers/webhooks">Webhooks</a>
  </nav>
  <div class="grid">
    {#if topic === 'overview'}
      <section class="card card-pad prose-card"><h2>Order lifecycle</h2><p>Service → quote → accepted quote → order → payment authorization → payment → fulfillment → verification → delivery → buyer acceptance. Each object remains distinct and each state change is persisted.</p><div class="lifecycle">Discover <b>→</b> Quote <b>→</b> Order <b>→</b> Authorize <b>→</b> Pay <b>→</b> Verify <b>→</b> Deliver</div></section>
      <section class="grid grid-2">
        <a class="card endpoint-card" href="http://localhost:43192/.well-known/thikra-services.json"><strong>Service manifest</strong><span>/.well-known/thikra-services.json</span><ExternalLink size={15} /></a>
        <a class="card endpoint-card" href="http://localhost:43192/.well-known/agent-card.json"><strong>A2A Agent Card</strong><span>/.well-known/agent-card.json</span><ExternalLink size={15} /></a>
        <a class="card endpoint-card" href="http://localhost:43192/.well-known/ucp"><strong>UCP profile</strong><span>/.well-known/ucp</span><ExternalLink size={15} /></a>
        <a class="card endpoint-card" href="http://localhost:43192/openapi.json"><strong>OpenAPI</strong><span>/openapi.json</span><ExternalLink size={15} /></a>
      </section>
      {#each ['curl', 'typescript', 'python'] as name}<section class="card code-card"><div><strong>{name}</strong><button class="btn btn-ghost" onclick={() => copy(name, snippets[name])}>{#if copied === name}<Check size={14} /> Copied{:else}<Copy size={14} /> Copy{/if}</button></div><pre>{snippets[name]}</pre></section>{/each}
    {:else if topic === 'mcp'}
      <section class="card card-pad prose-card"><h2>Streamable HTTP MCP</h2><p>Endpoint: <code>{apiBase}/mcp</code>. Send the same scoped bearer API key used by REST. The gateway exposes 17 tools and never returns payment credentials or storage secrets.</p><h3>Connection configuration</h3><div class="code-card"><div><strong>JSON</strong><button class="btn btn-ghost" onclick={() => copy('mcp', snippets.mcp)}><Copy size={14} /> Copy</button></div><pre>{snippets.mcp}</pre></div><h3>Safety boundaries</h3><ul><li>Human approval remains mandatory before payment.</li><li>Fulfillment cannot start before exact payment completion.</li><li>Generated assets are not deliverables until verification passes.</li><li>Downloads use authenticated, expiring URLs.</li></ul></section>
    {:else if topic === 'rest'}
      <section class="card card-pad prose-card"><h2>REST API v1</h2><p>Base URL: <code>{apiBase}/api/v1</code>. Mutation requests require a unique <code>Idempotency-Key</code>. Reusing a key with a different payload returns a stable conflict code.</p><h3>Authentication</h3><pre class="code-block">Authorization: Bearer thikra_test_…</pre><h3>Core endpoints</h3><div class="endpoint-list"><span>GET /services</span><span>POST /quotes</span><span>POST /quotes/:id/accept</span><span>POST /orders</span><span>POST /orders/:id/payment-authorization</span><span>POST /orders/:id/start</span><span>GET /orders/:id/deliverables</span></div><a class="btn btn-primary" href="http://localhost:43192/docs">Interactive OpenAPI docs <ExternalLink size={15} /></a></section>
    {:else}
      <section class="card card-pad prose-card"><h2>Signed webhooks</h2><p>Subscribe to quote, payment, fulfillment, review, delivery, dispute, and refund-request events. Callbacks require HTTPS and pass DNS/IP SSRF validation.</p><h3>Signature headers</h3><pre class="code-block">Thikra-Event-Id: event_uuid\nThikra-Timestamp: unix_seconds\nThikra-Signature: v1=hmac_sha256</pre><p>Verify the signature over <code>timestamp.canonical_json_body</code> and reject timestamps outside the five-minute replay window. Delivery retries use exponential backoff and disable repeatedly failing subscriptions.</p><a class="btn btn-primary" href="/webhooks">Manage subscriptions</a></section>
    {/if}
  </div>
</div>
