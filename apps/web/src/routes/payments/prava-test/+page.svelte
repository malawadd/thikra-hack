<script lang="ts">
  import { onMount } from 'svelte';
  import ExternalLink from 'lucide-svelte/icons/external-link';
  import KeyRound from 'lucide-svelte/icons/key-round';
  import RefreshCw from 'lucide-svelte/icons/refresh-cw';
  import ShieldCheck from 'lucide-svelte/icons/shield-check';
  import PageHeader from '$lib/components/PageHeader.svelte';
  import PravaCardForm from '$lib/components/PravaCardForm.svelte';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import { api, shortDate, titleCase } from '$lib/api/client';

  type Diagnostics = {
    mode: string;
    backend_url: string;
    secret_key_configured: boolean;
    publishable_key_configured: boolean;
    environment_matches_keys: boolean;
    health: { status?: string } | null;
    health_error: string | null;
    ready: boolean;
    issues: string[];
  };
  type TestSession = {
    session_id: string;
    session_token: string;
    iframe_url: string;
    order_id: string | null;
    expires_at: string;
    publishable_key: string;
    amount: string;
    currency: string;
  };
  type PollResult = {
    status: string;
    result: Record<string, unknown>;
    credential_generated: boolean;
  };
  type LogEntry = { time: string; event: string; detail: string };

  let diagnostics = $state<Diagnostics | null>(null);
  let session = $state<TestSession | null>(null);
  let result = $state<PollResult | null>(null);
  let loading = $state(true);
  let starting = $state(false);
  let polling = $state(false);
  let error = $state('');
  let cardComplete = $state(false);
  let iframeReady = $state(false);
  let logs = $state<LogEntry[]>([]);
  let pollTimer: number | undefined;
  let lastLoggedStatus = '';
  let browser = $state({ secureContext: false, passkeyApi: false, platformAuthenticator: null as boolean | null });

  function addLog(event: string, detail: string) {
    logs = [{ time: new Date().toLocaleTimeString(), event, detail }, ...logs].slice(0, 30);
  }

  function stopPolling() {
    if (pollTimer !== undefined) window.clearInterval(pollTimer);
    pollTimer = undefined;
    polling = false;
  }

  async function loadDiagnostics() {
    loading = true;
    error = '';
    try {
      diagnostics = await api<Diagnostics>('/thikra/prava-test');
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    } finally {
      loading = false;
    }
  }

  async function pollSession() {
    if (!session || polling) return;
    polling = true;
    try {
      const next = await api<PollResult>(`/thikra/prava-test/session/${session.session_id}/poll`, { method: 'POST', body: '{}' });
      result = next;
      if (next.status !== lastLoggedStatus) {
        lastLoggedStatus = next.status;
        addLog('Prava result', titleCase(next.status));
      }
      if (['completed', 'failed'].includes(next.status.toLowerCase())) stopPolling();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      error = message;
      addLog('Polling error', message);
    } finally {
      polling = false;
    }
  }

  function beginPolling() {
    stopPolling();
    void pollSession();
    pollTimer = window.setInterval(() => void pollSession(), 3000);
  }

  async function startSession() {
    starting = true;
    error = '';
    result = null;
    iframeReady = false;
    cardComplete = false;
    lastLoggedStatus = '';
    stopPolling();
    try {
      session = await api<TestSession>('/thikra/prava-test/session', { method: 'POST', body: '{}' });
      addLog('Session created', `${session.session_id} · expires ${shortDate(session.expires_at)}`);
      beginPolling();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
      addLog('Session error', error);
    } finally {
      starting = false;
    }
  }

  async function resetSession() {
    const previous = session;
    stopPolling();
    session = null;
    result = null;
    iframeReady = false;
    cardComplete = false;
    error = '';
    if (previous) {
      try {
        await api(`/thikra/prava-test/session/${previous.session_id}/revoke`, { method: 'POST', body: '{}' });
        addLog('Session revoked', previous.session_id);
      } catch (cause) {
        addLog('Revoke skipped', cause instanceof Error ? cause.message : String(cause));
      }
    }
  }

  async function requestFreshSession() {
    await resetSession();
    await startSession();
  }

  onMount(() => {
    browser.secureContext = window.isSecureContext;
    browser.passkeyApi = 'PublicKeyCredential' in window;
    if (browser.passkeyApi && PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) {
      void PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
        .then((available) => browser.platformAuthenticator = available)
        .catch(() => browser.platformAuthenticator = false);
    }
    void loadDiagnostics();
    return stopPolling;
  });
</script>

{#snippet actions()}<a class="btn btn-secondary" href="/payments">Payment ledger</a>{/snippet}
<PageHeader eyebrow="Prava sandbox diagnostics" title="One focused $0 payment test." description="Create one Prava sandbox session, complete card enrollment and hosted verification, and inspect every observable stage without authorizing provider spend." {actions} />

{#if error}<div class="error-box" role="alert" style="margin-bottom:18px">{error}</div>{/if}

<div class="grid grid-3" style="margin-bottom:18px">
  <article class="card card-pad">
    <div class="card-head-inline"><strong>Server configuration</strong><StatusBadge status={diagnostics?.ready ? 'READY' : 'CHECK'} /></div>
    <p class="help">{diagnostics?.backend_url ?? 'Checking Prava sandbox…'}</p>
    <div class="check-list">
      <div class="check" data-status={diagnostics?.environment_matches_keys ? 'PASS' : 'FAIL'}><div class="icon">{diagnostics?.environment_matches_keys ? '✓' : '!'}</div><div><h3>Sandbox keys match URL</h3><p>Test keys must target the sandbox API.</p></div></div>
      <div class="check" data-status={diagnostics?.health?.status === 'ok' ? 'PASS' : 'WARNING'}><div class="icon">{diagnostics?.health?.status === 'ok' ? '✓' : '○'}</div><div><h3>Prava API health</h3><p>{diagnostics?.health?.status ?? diagnostics?.health_error ?? 'Checking…'}</p></div></div>
    </div>
  </article>
  <article class="card card-pad">
    <div class="card-head-inline"><strong>Browser passkey readiness</strong><KeyRound size={18} /></div>
    <p class="help">Passkeys require a secure context. Localhost qualifies even without HTTPS.</p>
    <div class="check-list">
      <div class="check" data-status={browser.secureContext ? 'PASS' : 'FAIL'}><div class="icon">{browser.secureContext ? '✓' : '!'}</div><div><h3>Secure context</h3><p>{browser.secureContext ? 'Available' : 'Unavailable'}</p></div></div>
      <div class="check" data-status={browser.passkeyApi ? 'PASS' : 'FAIL'}><div class="icon">{browser.passkeyApi ? '✓' : '!'}</div><div><h3>WebAuthn API</h3><p>{browser.passkeyApi ? 'Supported by this browser' : 'Not supported'}</p></div></div>
      <div class="check" data-status={browser.platformAuthenticator ? 'PASS' : 'WARNING'}><div class="icon">{browser.platformAuthenticator ? '✓' : '○'}</div><div><h3>Platform authenticator</h3><p>{browser.platformAuthenticator === null ? 'Checking…' : browser.platformAuthenticator ? 'Available' : 'Not detected; a security key may still work'}</p></div></div>
    </div>
  </article>
  <article class="card card-pad">
    <div class="card-head-inline"><strong>Expected first-device flow</strong><ShieldCheck size={18} /></div>
    <ol class="help" style="padding-left:18px;line-height:1.8">
      <li>Complete the required shipping fields and enter a Prava-provided sandbox card.</li>
      <li>Accept the terms and click <strong>Pay Now</strong>; the displayed total remains `$0.00`.</li>
      <li>On a new browser/device, use sandbox OTP <strong>456789</strong>.</li>
      <li>Create or verify the passkey on Prava’s hosted screen.</li>
      <li>Wait for this page to report <strong>Completed</strong>.</li>
    </ol>
    <p class="help">The installed SDK exposes ready, card validation, success, and error events—but no separate “passkey shown” callback. The hosted Prava flow owns that prompt.</p>
  </article>
</div>

{#if loading}
  <div class="card loading"><div class="spinner"></div>Checking the Prava sandbox…</div>
{:else if !session}
  <section class="card card-pad" style="text-align:center;padding-block:42px">
    <span class="badge" data-tone="warning">$0.00 USD · sandbox only</span>
    <h2 style="margin:16px 0 7px">Card enrollment and passkey diagnostic</h2>
    <p class="lede" style="margin:0 auto 22px">This creates no provider-spend authorization and cannot launch a generation run.</p>
    <button class="btn btn-primary" onclick={startSession} disabled={starting || !diagnostics?.ready}>{starting ? 'Creating Prava session…' : 'Start $0 Prava test'}</button>
    {#if diagnostics?.issues.length}<div class="error-box" style="margin-top:18px;padding:18px">{diagnostics.issues.join(' · ')}</div>{/if}
  </section>
{:else}
  <div class="split-layout">
    <section>
      <div class="card card-pad" style="margin-bottom:18px">
        <div class="card-head-inline"><div><strong>Sandbox session</strong><div class="mono muted" style="margin-top:5px">{session.session_id}</div></div><StatusBadge status={result?.status ?? 'PENDING'} /></div>
        <div class="actions" style="margin-top:16px">
          <a class="btn btn-secondary" href={session.iframe_url} target="_blank" rel="noreferrer">Open hosted flow <ExternalLink size={15} /></a>
          <button class="btn btn-secondary" onclick={pollSession} disabled={polling}><RefreshCw size={15} /> {polling ? 'Checking…' : 'Check now'}</button>
          <button class="btn btn-danger" onclick={resetSession}>End test</button>
        </div>
        <p class="help">If the embedded flow never reaches the passkey screen, use “Open hosted flow.” It keeps the same session while removing cross-origin iframe and browser-extension variables.</p>
        <p class="help"><strong>The passkey prompt is not the first screen.</strong> Prava shows it only after its required address/card fields, terms checkbox, Pay Now submission, and—on a new device—the OTP step.</p>
      </div>
      <PravaCardForm
        session={session}
        publishableKey={session.publishable_key}
        onready={() => { iframeReady = true; addLog('Iframe ready', 'Prava secure card form mounted'); }}
        onchange={(state) => { if (state?.isComplete && !cardComplete) { cardComplete = true; addLog('Card fields valid', 'Waiting for hosted OTP/passkey flow'); } }}
        onsuccess={() => { addLog('SDK success', 'Prava reported successful card collection'); void pollSession(); }}
        onerror={(message) => { error = message; addLog('SDK error', message); }}
        onnewsession={requestFreshSession}
      />
    </section>
    <aside class="sticky-panel">
      <section class="card card-pad">
        <div class="card-head-inline"><strong>Live observations</strong><span class="badge" data-tone={iframeReady ? 'success' : 'info'}>{iframeReady ? 'Iframe ready' : 'Mounting'}</span></div>
        <div class="timeline" style="margin-top:14px">
          {#each logs as entry}
            <div class="timeline-item"><div class="timeline-dot"></div><div><h3>{entry.event}</h3><p>{entry.time} · {entry.detail}</p></div></div>
          {:else}
            <p class="help">Events will appear as the session progresses.</p>
          {/each}
        </div>
      </section>
      {#if result}
        <section class="card card-pad section">
          <div class="card-head-inline"><strong>Sanitized Prava result</strong><StatusBadge status={result.status} /></div>
          <p class="help">Network tokens and dynamic CVVs are stripped server-side and never rendered here.</p>
          <pre class="code-block">{JSON.stringify(result.result, null, 2)}</pre>
        </section>
      {/if}
    </aside>
  </div>
{/if}
