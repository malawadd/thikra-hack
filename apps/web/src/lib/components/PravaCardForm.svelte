<script lang="ts">
  import { onMount } from 'svelte';
  import CreditCard from 'lucide-svelte/icons/credit-card'; import LockKeyhole from 'lucide-svelte/icons/lock-keyhole';

  let {
    session,
    publishableKey,
    onerror,
    onnewsession,
    onready,
    onchange,
    onsuccess
  }: {
    session: { session_token: string; iframe_url: string };
    publishableKey: string;
    onerror?: (message: string) => void;
    onnewsession?: () => void | Promise<void>;
    onready?: () => void;
    onchange?: (state: any) => void;
    onsuccess?: (result: any) => void;
  } = $props();
  let container: HTMLDivElement;
  let loading = $state(true);
  let ready = $state(false);
  let error = $state('');
  let validation = $state<any>(null);
  let requestingSession = $state(false);
  let sdk: any = null;
  let readyNotified = false;

  function markReady() {
    ready = true;
    loading = false;
    if (!readyNotified) {
      readyNotified = true;
      onready?.();
    }
  }

  function errorMessage(cause: unknown): string {
    const value = cause && typeof cause === 'object' && 'message' in cause
      ? (cause as { message?: unknown }).message
      : cause;
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object' && 'message' in value) {
      const nested = (value as { message?: unknown }).message;
      if (typeof nested === 'string') return nested;
    }
    try { return JSON.stringify(value); } catch { return 'Prava authorization failed.'; }
  }

  async function mount() {
    loading = true;
    ready = false;
    readyNotified = false;
    validation = null;
    error = '';
    sdk?.destroy();
    try {
      const { PravaSDK } = await import('@prava-sdk/core');
      sdk = new PravaSDK({ publishableKey });
      await sdk.collectPAN({
        sessionToken: session.session_token,
        iframeUrl: session.iframe_url,
        container,
        onReady: markReady,
        onChange: (state: any) => { validation = state; onchange?.(state); },
        onSuccess: (result: any) => onsuccess?.(result),
        onError: (cause: unknown) => { error = errorMessage(cause); loading = false; onerror?.(error); }
      });
    } catch (cause) {
      error = errorMessage(cause);
      loading = false;
      onerror?.(error);
    }
  }

  async function requestNewSession() {
    if (!onnewsession) return;
    requestingSession = true;
    try { await onnewsession(); }
    finally { requestingSession = false; }
  }

  onMount(() => {
    void mount();
    const observer = new MutationObserver(() => {
      if (container?.querySelector('iframe')) markReady();
    });
    observer.observe(container, { childList: true, subtree: true });
    const timeout = window.setTimeout(() => loading = false, 5000);
    return () => { observer.disconnect(); window.clearTimeout(timeout); sdk?.destroy(); sdk = null; };
  });
</script>

<div class="card card-pad">
  <div style="display:flex;gap:12px;align-items:center;margin-bottom:15px"><LockKeyhole size={19} /><div><strong>Prava secure authorization</strong><div class="small muted">Card data stays inside Prava's PCI-compliant iframe.</div></div></div>
  {#if error}
    <div class="error-box">
      <span>{error}</span>
      <div class="actions">
        {#if onnewsession}<button class="btn btn-primary" onclick={requestNewSession} disabled={requestingSession}>{requestingSession ? 'Creating session…' : 'Request new session'}</button>{/if}
        <button class="btn btn-secondary" onclick={mount} disabled={requestingSession}>Reload current iframe</button>
      </div>
    </div>
  {/if}
  {#if loading && !error}<div class="loading"><div class="spinner"></div>Loading secure card form…</div>{/if}
  {#if validation && ready}
    <div class="actions" style="margin-bottom:10px">
      <span class="badge" data-tone={validation.cardNumber?.isValid ? 'success' : ''}><CreditCard size={12} /> Card</span>
      <span class="badge" data-tone={validation.expiry?.isValid ? 'success' : ''}>Expiry</span>
      <span class="badge" data-tone={validation.cvv?.isValid ? 'success' : ''}>CVV</span>
    </div>
  {/if}
  <div bind:this={container} style="min-height:400px;overflow:hidden;border-radius:14px"></div>
</div>
