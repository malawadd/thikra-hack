<script lang="ts">
  import '../app.css';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { api } from '$lib/api/client';
  import FolderOpen from 'lucide-svelte/icons/folder-open'; import Blocks from 'lucide-svelte/icons/blocks'; import BriefcaseBusiness from 'lucide-svelte/icons/briefcase-business'; import CircleDollarSign from 'lucide-svelte/icons/circle-dollar-sign'; import FilePlus2 from 'lucide-svelte/icons/file-plus-2'; import Gauge from 'lucide-svelte/icons/gauge'; import Menu from 'lucide-svelte/icons/menu'; import Network from 'lucide-svelte/icons/network'; import PlaySquare from 'lucide-svelte/icons/square-play'; import ScrollText from 'lucide-svelte/icons/scroll-text'; import ShieldCheck from 'lucide-svelte/icons/shield-check'; import Sparkles from 'lucide-svelte/icons/sparkles'; import Store from 'lucide-svelte/icons/store'; import Bot from 'lucide-svelte/icons/bot'; import KeyRound from 'lucide-svelte/icons/key-round'; import Webhook from 'lucide-svelte/icons/webhook'; import X from 'lucide-svelte/icons/x';
  let { children } = $props();
  let menuOpen = $state(false);
  let mode = $state('DEMO');
  let backendReady = $state(false);
  let hydrated = $state(false);
  onMount(async () => {
    hydrated = true;
    try { const health = await api<{ mode: string }>('/health/ready'); mode = health.mode; backendReady = true; }
    catch { backendReady = false; }
  });
  const nav = [
    { href: '/', label: 'Overview', icon: Gauge },
    { href: '/services', label: 'Marketplace', icon: Store },
    { href: '/developers', label: 'Agent Gateway', icon: Bot },
    { href: '/commercial-orders', label: 'Commercial Orders', icon: BriefcaseBusiness },
    { href: '/developer-applications', label: 'Developer Apps', icon: KeyRound },
    { href: '/webhooks', label: 'Webhooks', icon: Webhook },
    { href: '/briefs/new', label: 'New Brief', icon: FilePlus2 },
    { href: '/runs', label: 'Runs', icon: PlaySquare },
    { href: '/assets', label: 'Asset Library', icon: FolderOpen },
    { href: '/evidence', label: 'Evidence', icon: Network },
    { href: '/payments', label: 'Payments', icon: CircleDollarSign },
    { href: '/cases', label: 'Cases', icon: BriefcaseBusiness },
    { href: '/integrations', label: 'Integrations', icon: Blocks }
  ];
  const active = (href: string) => href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(href);
  function navigate(href: string) { menuOpen = false; void goto(href); }
</script>

<svelte:head><title>Thikra · Verify-Then-Pay Creative Commerce</title></svelte:head>

<div class="app-shell" data-hydrated={hydrated}>
  <aside class:open={menuOpen} class="sidebar" aria-label="Primary navigation">
    <div class="brand">
      <div class="brand-mark"><Sparkles size={21} /></div>
      <div class="brand-copy"><strong>Thikra</strong><small>Creative commerce</small></div>
    </div>
    <nav class="nav">
      {#each nav as item}
        <a href={item.href} class:active={active(item.href)} onclick={(event) => { event.preventDefault(); navigate(item.href); }}>
          <span class="nav-icon"><item.icon size={17} /></span><span>{item.label}</span>
        </a>
      {/each}
    </nav>
    <div class="sidebar-foot">
      <strong class="sidebar-foot-head"><ShieldCheck size={14} /> {mode} environment</strong>
      <p>{mode === 'DEMO' ? 'Payment and generation fixtures are simulated and visibly labeled. No real transaction is claimed.' : 'Configured integrations are used through server-only credentials and bounded policy.'}</p>
    </div>
  </aside>
  <main class="main">
    <div class="topbar">
      <button class="mobile-menu" aria-label={menuOpen ? 'Close navigation' : 'Open navigation'} onclick={() => menuOpen = !menuOpen}>
        {#if menuOpen}<X size={22} />{:else}<Menu size={22} />{/if}
      </button>
      <div class="env topbar-status"><span class="pulse" class:offline={!backendReady}></span>{backendReady ? 'Backend evidence online' : 'Backend unavailable'}</div>
      <div class="actions topbar-assurance"><span class="badge" data-tone="info"><ScrollText size={12} /> Audit chain active</span></div>
    </div>
    <div class="content">{@render children()}</div>
  </main>
</div>
