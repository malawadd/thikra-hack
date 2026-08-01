<script lang="ts">
  import { onMount } from 'svelte';
  import CreditCard from 'lucide-svelte/icons/credit-card'; import LockKeyhole from 'lucide-svelte/icons/lock-keyhole';

  let {
    session,
    publishableKey,
    onerror
  }: { session: { session_token: string; iframe_url: string }; publishableKey: string; onerror?: (message: string) => void } = $props();
  let container: HTMLDivElement;
  let loading = $state(true);
  let ready = $state(false);
  let error = $state('');
  let validation = $state<any>(null);
  let sdk: any = null;

  async function mount() {
    loading = true;
    error = '';
    sdk?.destroy();
    try {
      const { PravaSDK } = await import('@prava-sdk/core');
      sdk = new PravaSDK({ publishableKey });
      await sdk.collectPAN({
        sessionToken: session.session_token,
        iframeUrl: session.iframe_url,
        container,
        onReady: () => { ready = true; loading = false; },
        onChange: (state: any) => validation = state,
        onSuccess: () => {},
        onError: (cause: any) => { error = cause.message; loading = false; onerror?.(cause.message); }
      });
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
      loading = false;
      onerror?.(error);
    }
  }

  onMount(() => {
    void mount();
    const observer = new MutationObserver(() => {
      if (container?.querySelector('iframe')) { ready = true; loading = false; }
    });
    observer.observe(container, { childList: true, subtree: true });
    const timeout = window.setTimeout(() => loading = false, 5000);
    return () => { observer.disconnect(); window.clearTimeout(timeout); sdk?.destroy(); sdk = null; };
  });
</script>

<div class="card card-pad">
  <div style="display:flex;gap:12px;align-items:center;margin-bottom:15px"><LockKeyhole size={19} /><div><strong>Prava secure authorization</strong><div class="small muted">Card data stays inside Prava's PCI-compliant iframe.</div></div></div>
  {#if error}<div class="error-box"><span>{error}</span><button class="btn btn-secondary" onclick={mount}>Try again</button></div>{/if}
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
