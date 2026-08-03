<script lang="ts">
  import { onMount } from 'svelte';
  import ArrowRight from 'lucide-svelte/icons/arrow-right';
  import Blocks from 'lucide-svelte/icons/blocks';
  import Bot from 'lucide-svelte/icons/bot';
  import Check from 'lucide-svelte/icons/check';
  import FilePlus2 from 'lucide-svelte/icons/file-plus-2';
  import FolderOpen from 'lucide-svelte/icons/folder-open';
  import Network from 'lucide-svelte/icons/network';
  import PlaySquare from 'lucide-svelte/icons/square-play';
  import ScrollText from 'lucide-svelte/icons/scroll-text';
  import ShieldCheck from 'lucide-svelte/icons/shield-check';
  import Sparkles from 'lucide-svelte/icons/sparkles';

  type Surface = 'agents' | 'studio' | 'web';

  let activeSurface = $state<Surface>('agents');
  let panelMotion = $state(false);
  let landingRoot: HTMLDivElement;

  function selectSurface(event: MouseEvent, surface: Surface) {
    panelMotion = event.detail > 0;
    activeSurface = surface;
  }

  function showStudio(event: MouseEvent) {
    selectSurface(event, 'studio');
  }

  onMount(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduceMotion) return;

    const targets = Array.from(landingRoot.querySelectorAll<HTMLElement>('[data-reveal]'));
    targets.forEach((target, index) => {
      target.classList.add('reveal-pending');
      target.style.setProperty('--reveal-delay', `${(index % 3) * 50}ms`);
    });

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        (entry.target as HTMLElement).classList.add('reveal-visible');
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.14, rootMargin: '0px 0px -7% 0px' });

    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  });
</script>

<svelte:head>
  <title>Thikra · Agent-native creative infrastructure</title>
  <meta name="description" content="Thikra turns agent intent into finished, verifiable media with Genblaze orchestration, Backblaze B2 storage, and a real desktop creative Studio." />
</svelte:head>

<div class="landing" bind:this={landingRoot}>
  <header class="marketing-header">
    <a class="marketing-brand" href="/" aria-label="Thikra home">
      <span class="marketing-mark"><Sparkles size={19} /></span>
      <span><strong>Thikra</strong><small>Creative infrastructure</small></span>
    </a>
    <nav aria-label="Landing navigation">
      <a href="#agents">Agents</a>
      <a href="#studio" onclick={showStudio}>Studio</a>
      <a href="#infrastructure">B2 + Genblaze</a>
      <a href="#workflow">How it works</a>
    </nav>
    <div class="marketing-actions">
      <a class="console-link" href="/overview">Web console</a>
      <a class="marketing-button compact" href="#studio" onclick={showStudio}>Explore Studio <ArrowRight size={14} /></a>
    </div>
  </header>

  <main>
    <section class="hero">
      <div class="hero-glow glow-left"></div>
      <div class="hero-glow glow-right"></div>
      <div class="hero-copy hero-enter">
        <div class="hero-eyebrow"><span></span> Agent-native creative infrastructure</div>
        <h1>From agent intent to <em>finished media.</em></h1>
        <p class="hero-accent">Powered by Genblaze. Grounded in B2.</p>
        <p class="hero-lede">Thikra lets agents commission governed creative work, routes each modality through a per-run provider switchboard, brings every asset into a real desktop Studio, and delivers results with durable evidence.</p>
        <div class="hero-actions">
          <a class="marketing-button" href="#studio" onclick={showStudio}>Explore Thikra Studio <ArrowRight size={16} /></a>
          <a class="marketing-button secondary" href="/developers"><Bot size={16} /> Connect an agent</a>
        </div>
        <div class="hero-note"><ShieldCheck size={14} /> Mandates, budgets, lineage, verification, and delivery stay explicit.</div>
      </div>

      <div class="agent-hero hero-enter" aria-label="Agent creative workflow preview">
        <div class="window-bar">
          <span class="window-dots"><i></i><i></i><i></i></span>
          <span>thikra_agent_session</span>
          <span class="live-label"><i></i> evidence live</span>
        </div>
        <div class="agent-workspace">
          <div class="conversation">
            <span class="panel-label">AGENT REQUEST</span>
            <div class="request-bubble">
              <div class="avatar"><Bot size={17} /></div>
              <p>Create a verified Arabic vertical campaign for Noura Glow. Keep spend under $5 and preserve every source asset.</p>
            </div>
            <div class="agent-answer">
              <span><Sparkles size={14} /> Thikra</span>
              <p>I found a compliant service, compiled the mandate, and prepared a bounded provider strategy.</p>
              <div class="answer-chips"><span>MCP</span><span>REST</span><span>A2A</span><span>UCP</span></div>
            </div>
          </div>
          <div class="execution-card">
            <div class="execution-head"><div><span class="panel-label">LIVE EXECUTION</span><strong>Campaign pipeline</strong></div><span class="run-state">RUNNING</span></div>
            <div class="execution-steps">
              <div class="complete"><span><Check size={12} /></span><div><strong>Mandate confirmed</strong><small>Policy and budget versioned</small></div></div>
              <div class="complete"><span><Check size={12} /></span><div><strong>Genblaze providers</strong><small>Image · video · voice · music</small></div></div>
              <div class="active"><span><Sparkles size={12} /></span><div><strong>Assets generating</strong><small>Parent lineage preserved</small></div></div>
              <div><span><FolderOpen size={12} /></span><div><strong>B2 delivery</strong><small>Manifest, exports, evidence</small></div></div>
            </div>
            <div class="asset-result">
              <div class="asset-art"><span>NOURA</span><strong>GLOW</strong></div>
              <div><small>FINAL ASSET</small><strong>vertical-ad-v3.mp4</strong><span><ShieldCheck size={12} /> verification ready</span></div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="ecosystem" aria-label="Thikra ecosystem">
      <span>Built around real creative infrastructure</span>
      <div><strong>Genblaze</strong><i></i><strong>Backblaze B2</strong><i></i><strong>Thikra Studio</strong><i></i><strong>MCP + REST</strong></div>
    </section>

    <section class="infrastructure landing-section" id="infrastructure">
      <div class="section-heading" data-reveal>
        <span class="section-kicker">THE INFRASTRUCTURE DUO</span>
        <h2>Generation that stays useful after the model responds.</h2>
        <p>Genblaze gives Thikra a consistent multimodal execution layer. B2 gives every output a durable place in the creative record.</p>
      </div>
      <div class="partner-grid">
        <article class="partner-card genblaze-card" data-reveal>
          <div class="partner-icon"><Blocks size={22} /></div>
          <span class="partner-number">01</span>
          <h3>Genblaze orchestrates the creative run.</h3>
          <p>Choose providers per modality and per run while keeping typed assets, manifests, parent lineage, retries, and failure boundaries consistent.</p>
          <div class="modality-row"><span>IMAGE</span><span>VIDEO</span><span>VOICE</span><span>MUSIC</span></div>
          <div class="provider-lines"><i></i><i></i><i></i><i></i></div>
        </article>
        <article class="partner-card b2-card" data-reveal>
          <div class="partner-icon"><FolderOpen size={22} /></div>
          <span class="partner-number">02</span>
          <h3>B2 makes the output durable.</h3>
          <p>Generated sources, Genblaze manifests, proxy media, rendered MP4/SRT exports, and final deliverables remain connected to hashes and evidence.</p>
          <div class="object-stack">
            <span><FilePlus2 size={14} /> scene-03-keyframe.webp <b>stored</b></span>
            <span><PlaySquare size={14} /> final-vertical-ad.mp4 <b>verified</b></span>
            <span><ScrollText size={14} /> delivery-manifest.json <b>signed</b></span>
          </div>
        </article>
      </div>
    </section>

    <section class="surfaces landing-section" id="agents">
      <div class="section-heading centered" data-reveal>
        <span class="section-kicker">ONE SYSTEM · THREE SURFACES</span>
        <h2>Commission, create, and prove the work.</h2>
        <p>Agents lead the transaction. Studio finishes the creative. The web console keeps the operational record available when it matters.</p>
      </div>

      <div class="surface-tabs" role="tablist" aria-label="Thikra product surfaces" data-reveal>
        <button role="tab" aria-selected={activeSurface === 'agents'} aria-controls="surface-agents" onclick={(event) => selectSurface(event, 'agents')}><Bot size={16} /> Agents</button>
        <button role="tab" aria-selected={activeSurface === 'studio'} aria-controls="surface-studio" onclick={(event) => selectSurface(event, 'studio')}><Sparkles size={16} /> Studio</button>
        <button role="tab" aria-selected={activeSurface === 'web'} aria-controls="surface-web" onclick={(event) => selectSurface(event, 'web')}><Network size={16} /> Web console</button>
      </div>

      <div class="surface-panels" data-motion={panelMotion} data-reveal>
        <section id="surface-agents" class="surface-panel agent-panel" class:surface-active={activeSurface === 'agents'} role="tabpanel" aria-hidden={activeSurface !== 'agents'} inert={activeSurface !== 'agents'}>
          <div class="surface-copy">
            <span class="section-kicker">AGENT GATEWAY</span>
            <h3>Creative commerce an agent can actually complete.</h3>
            <p>Discover deterministic services, obtain quotes, request bounded authorization, start fulfillment, and retrieve verified deliverables through one contract.</p>
            <ul><li><Check size={14} /> MCP, REST, A2A, and UCP discovery</li><li><Check size={14} /> Idempotent commercial actions</li><li><Check size={14} /> Evidence-backed delivery and redress</li></ul>
            <a href="/developers">Open the Agent Gateway <ArrowRight size={14} /></a>
          </div>
          <div class="gateway-visual">
            <div class="gateway-code"><span>thikra_create_order</span><pre>{`{
  "service": "verified-vertical-ad",
  "budget_cap": 500,
  "language": "ar",
  "evidence_required": true
}`}</pre></div>
            <div class="gateway-arrow"><ArrowRight size={18} /></div>
            <div class="gateway-receipt"><ShieldCheck size={22} /><strong>Verified delivery</strong><span>B2 object + manifest</span><span>Payment-to-delivery receipt</span><span>Human acceptance recorded</span></div>
          </div>
        </section>

        <section id="surface-studio" class="surface-panel studio-panel" class:surface-active={activeSurface === 'studio'} role="tabpanel" aria-hidden={activeSurface !== 'studio'} inert={activeSurface !== 'studio'}>
          <div class="studio-window" id="studio">
            <div class="studio-top"><span><Sparkles size={14} /> Thikra Studio</span><div><button type="button">Generate</button><button class="selected" type="button">Edit</button></div><span>Noura Glow · revision 12</span></div>
            <div class="studio-body">
              <aside><strong>PROJECT MEDIA</strong><div class="media-thumb mint-thumb"></div><div class="media-thumb blue-thumb"></div><div class="media-thumb dark-thumb"></div></aside>
              <div class="studio-center">
                <video src="/demo/noura-glow.mp4" controls playsinline preload="metadata"><track kind="captions" src="/demo/captions.vtt" srclang="ar" label="Arabic" /></video>
                <div class="transport"><button type="button">▶</button><span>00:08.4 / 00:15.0</span><i></i></div>
                <div class="mini-timeline"><span class="playhead"></span><div class="track video-track"><i></i><i></i><i></i></div><div class="track audio-track"><i></i></div><div class="track caption-track"><i></i><i></i></div></div>
              </div>
              <aside class="inspector"><strong>INSPECTOR</strong><div class="inspector-row">Scale<span>100%</span></div><i></i><div class="inspector-row">Opacity<span>100%</span></div><i></i><div class="inspector-row">Transition<span>Dissolve</span></div></aside>
            </div>
          </div>
          <div class="studio-caption"><span class="section-kicker">THIKRA STUDIO</span><h3>Generate in a typed node graph. Finish in a real timeline.</h3><p>Reversible agent proposals, variant review, shared project media, non-destructive editing, and revision-pinned B2 exports live in one Windows-first workspace.</p></div>
        </section>

        <section id="surface-web" class="surface-panel web-panel" class:surface-active={activeSurface === 'web'} role="tabpanel" aria-hidden={activeSurface !== 'web'} inert={activeSurface !== 'web'}>
          <div class="surface-copy">
            <span class="section-kicker">OPERATIONS CONSOLE</span>
            <h3>The receipts stay visible after generation.</h3>
            <p>Review mandates, spend, providers, run state, asset lineage, verification checks, human decisions, and redress without turning the console into the product’s front door.</p>
            <a href="/overview">Open the web console <ArrowRight size={14} /></a>
          </div>
          <div class="console-visual">
            <div class="console-stats"><span><small>AUTHORIZED</small><strong>$5.00</strong></span><span><small>SPENT</small><strong>$3.84</strong></span><span><small>STATUS</small><strong>VERIFIED</strong></span></div>
            <div class="console-evidence"><strong>Evidence chain</strong><span><i></i> Mandate confirmed <b>PASS</b></span><span><i></i> Provider output stored <b>PASS</b></span><span><i></i> Arabic narration verified <b>PASS</b></span><span><i></i> Human acceptance <b>RECORDED</b></span></div>
          </div>
        </section>
      </div>
    </section>

    <section class="workflow landing-section" id="workflow">
      <div class="section-heading" data-reveal>
        <span class="section-kicker">A COMPLETE CREATIVE LOOP</span>
        <h2>Every stage has a job. Every handoff leaves evidence.</h2>
      </div>
      <div class="workflow-grid">
        <article data-reveal><span>01</span><div class="workflow-icon"><Bot size={19} /></div><h3>Commission</h3><p>An agent discovers a service, compiles intent into policy, and requests a bounded commercial action.</p></article>
        <article data-reveal><span>02</span><div class="workflow-icon"><Blocks size={19} /></div><h3>Generate</h3><p>Genblaze runs the selected image, video, voice, and music providers while preserving lineage.</p></article>
        <article data-reveal><span>03</span><div class="workflow-icon"><Sparkles size={19} /></div><h3>Finish</h3><p>Creators review variants and refine the result in Studio’s node canvas and multi-track editor.</p></article>
        <article data-reveal><span>04</span><div class="workflow-icon"><ShieldCheck size={19} /></div><h3>Deliver</h3><p>B2-backed assets move through verification, acceptance, signed delivery, and redress.</p></article>
      </div>
    </section>

    <section class="closing" data-reveal>
      <div class="closing-glow"></div>
      <span class="section-kicker">THIKRA CREATIVE INFRASTRUCTURE</span>
      <h2>Give agents a creative system.<br />Give creators the final say.</h2>
      <p>Start with the Agent Gateway, then bring the work into Studio when it deserves a human finish.</p>
      <div class="hero-actions"><a class="marketing-button dark" href="#studio" onclick={showStudio}>Explore Studio <ArrowRight size={16} /></a><a class="marketing-button light" href="/developers">Connect an agent <Bot size={16} /></a></div>
    </section>
  </main>

  <footer>
    <a class="marketing-brand" href="/"><span class="marketing-mark"><Sparkles size={17} /></span><span><strong>Thikra</strong><small>Agent-native creative infrastructure</small></span></a>
    <div><a href="#agents">Agents</a><a href="#studio" onclick={showStudio}>Studio</a><a href="#infrastructure">B2 + Genblaze</a><a href="/overview">Web console</a></div>
    <p>Genblaze orchestration · Backblaze B2 durability · Human-verifiable delivery</p>
  </footer>
</div>

<style>
  .landing { min-height: 100vh; overflow: hidden; color: #1c2d27; background: #fbfaf6; }
  .marketing-header { position: sticky; top: 0; z-index: 50; width: min(1240px, calc(100% - 36px)); min-height: 70px; margin: 14px auto 0; padding: 0 10px 0 14px; display: flex; align-items: center; justify-content: space-between; gap: 24px; border: 1px solid rgba(191, 205, 195, .72); border-radius: 18px; background: rgba(255, 254, 250, .82); box-shadow: 0 10px 32px rgba(35, 52, 46, .06); backdrop-filter: blur(18px) saturate(140%); }
  .marketing-brand { display: inline-flex; align-items: center; gap: 10px; flex: 0 0 auto; }
  .marketing-mark { width: 36px; height: 36px; display: grid; place-items: center; border: 1px solid rgba(17, 104, 78, .13); border-radius: 12px; background: linear-gradient(145deg, #85e3bd, #63cda5); color: #164838; box-shadow: inset 0 1px rgba(255,255,255,.45), 0 7px 17px rgba(42,137,102,.12); }
  .marketing-brand strong, .marketing-brand small { display: block; }
  .marketing-brand strong { font-size: .98rem; letter-spacing: -.035em; }
  .marketing-brand small { margin-top: 2px; color: #7a8580; font-size: .54rem; font-weight: 750; letter-spacing: .11em; text-transform: uppercase; }
  .marketing-header nav { display: flex; align-items: center; gap: 28px; }
  .marketing-header nav a, .console-link { color: #5f6d67; font-size: .73rem; font-weight: 700; transition: color 160ms ease; }
  .marketing-actions { display: flex; align-items: center; gap: 13px; }
  .marketing-button { min-height: 46px; padding: 11px 17px; display: inline-flex; align-items: center; justify-content: center; gap: 8px; border: 1px solid #55c49b; border-radius: 13px; background: linear-gradient(180deg, #7fe0b8, #68d0a8); color: #153f32; box-shadow: inset 0 1px rgba(255,255,255,.42), 0 9px 24px rgba(41,151,110,.17); font-size: .77rem; font-weight: 800; transition: transform 140ms var(--ease-out), box-shadow 180ms ease, border-color 160ms ease, background-color 160ms ease; }
  .marketing-button.compact { min-height: 40px; padding: 9px 13px; font-size: .7rem; }
  .marketing-button.secondary { border-color: #d8dfd7; background: rgba(255,254,250,.84); color: #263730; box-shadow: 0 4px 14px rgba(35,52,46,.045); }
  .marketing-button.dark { border-color: #17251f; background: #17251f; color: #f8fbf8; box-shadow: 0 10px 28px rgba(13,27,21,.18); }
  .marketing-button.light { border-color: rgba(255,255,255,.6); background: rgba(255,255,255,.86); color: #1d3128; box-shadow: none; }
  .marketing-button:active { transform: scale(.98); }

  .hero { position: relative; width: min(1240px, calc(100% - 36px)); min-height: 760px; margin: 0 auto; padding: 112px 0 82px; display: grid; grid-template-columns: minmax(0, .88fr) minmax(560px, 1.12fr); align-items: center; gap: 68px; }
  .hero-glow { position: absolute; border-radius: 50%; filter: blur(3px); pointer-events: none; }
  .glow-left { width: 520px; height: 520px; left: -310px; top: 20px; background: radial-gradient(circle, rgba(106,220,178,.2), transparent 68%); }
  .glow-right { width: 640px; height: 640px; right: -360px; top: -90px; background: radial-gradient(circle, rgba(129,185,255,.2), rgba(213,168,255,.08) 45%, transparent 70%); }
  .hero-copy { position: relative; z-index: 2; }
  .hero-eyebrow { display: flex; align-items: center; gap: 8px; color: #177358; font-size: .66rem; font-weight: 850; letter-spacing: .145em; text-transform: uppercase; }
  .hero-eyebrow span { width: 7px; height: 7px; border-radius: 50%; background: #52c594; box-shadow: 0 0 0 5px rgba(82,197,148,.12); }
  .hero h1 { max-width: 640px; margin: 18px 0 0; font-size: clamp(3.4rem, 6.2vw, 6.15rem); line-height: .91; font-weight: 660; letter-spacing: -.075em; text-wrap: balance; }
  .hero h1 em { color: #2d9372; font-style: normal; }
  .hero-accent { margin: 25px 0 0; font-size: clamp(1rem, 1.6vw, 1.28rem); font-weight: 780; letter-spacing: -.025em; }
  .hero-lede { max-width: 590px; margin: 14px 0 0; color: #67756f; font-size: .92rem; line-height: 1.72; text-wrap: pretty; }
  .hero-actions { margin-top: 28px; display: flex; flex-wrap: wrap; gap: 10px; }
  .hero-note { margin-top: 18px; display: flex; align-items: center; gap: 7px; color: #718079; font-size: .67rem; }
  .hero-enter { animation: hero-rise 280ms var(--ease-out) both; }
  .agent-hero.hero-enter { animation-delay: 60ms; }
  @keyframes hero-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

  .agent-hero { position: relative; z-index: 2; overflow: hidden; border: 1px solid #d4ddd5; border-radius: 25px; background: rgba(255,254,250,.9); box-shadow: 0 2px 5px rgba(29,50,40,.04), 0 32px 80px rgba(42,65,54,.12); transform: rotate(.65deg); }
  .agent-hero::before { content: ''; position: absolute; inset: -70px auto auto -70px; width: 230px; height: 230px; border-radius: 50%; background: rgba(112,215,176,.14); pointer-events: none; }
  .window-bar { height: 48px; padding: 0 15px; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; border-bottom: 1px solid #dfe5de; background: rgba(246,248,243,.82); color: #7a8781; font: .62rem Consolas, monospace; }
  .window-dots { display: flex; gap: 5px; }
  .window-dots i { width: 7px; height: 7px; border-radius: 50%; background: #d5dcd4; }
  .live-label { justify-self: end; display: flex; align-items: center; gap: 5px; color: #347660; }
  .live-label i { width: 6px; height: 6px; border-radius: 50%; background: #44bd88; box-shadow: 0 0 0 4px rgba(68,189,136,.11); }
  .agent-workspace { min-height: 475px; padding: 18px; display: grid; grid-template-columns: .86fr 1.14fr; gap: 14px; background: radial-gradient(circle at 10% 15%, rgba(225,246,237,.72), transparent 15rem), #fbfaf6; }
  .conversation, .execution-card { position: relative; border: 1px solid #dde4dc; border-radius: 17px; background: rgba(255,254,250,.91); box-shadow: 0 9px 26px rgba(35,52,46,.045); }
  .conversation { padding: 17px; }
  .panel-label { color: #849089; font-size: .53rem; font-weight: 850; letter-spacing: .12em; }
  .request-bubble { margin-top: 12px; padding: 13px; display: flex; gap: 10px; border-radius: 13px 13px 4px 13px; background: #e6f6ee; color: #2a3e35; }
  .request-bubble p { margin: 0; font-size: .69rem; line-height: 1.55; }
  .avatar { width: 29px; height: 29px; flex: 0 0 auto; display: grid; place-items: center; border-radius: 10px; background: #70d7b0; color: #154b3a; }
  .agent-answer { margin-top: 17px; }
  .agent-answer > span { display: flex; align-items: center; gap: 6px; color: #17684e; font-size: .67rem; font-weight: 800; }
  .agent-answer p { margin: 8px 0 0; color: #69766f; font-size: .66rem; line-height: 1.55; }
  .answer-chips { margin-top: 15px; display: flex; flex-wrap: wrap; gap: 5px; }
  .answer-chips span, .modality-row span { padding: 5px 7px; border: 1px solid #dce5dd; border-radius: 7px; background: #f2f6f2; color: #506158; font-size: .51rem; font-weight: 800; letter-spacing: .05em; }
  .execution-card { padding: 17px; }
  .execution-head { display: flex; justify-content: space-between; gap: 10px; }
  .execution-head strong { display: block; margin-top: 5px; font-size: .82rem; }
  .run-state { align-self: start; padding: 5px 7px; border-radius: 999px; background: #e4f5ed; color: #17684e; font-size: .5rem; font-weight: 850; letter-spacing: .06em; }
  .execution-steps { position: relative; margin-top: 17px; display: grid; gap: 8px; }
  .execution-steps::before { content: ''; position: absolute; left: 12px; top: 24px; bottom: 24px; width: 1px; background: #dbe2db; }
  .execution-steps > div { position: relative; display: grid; grid-template-columns: 25px 1fr; gap: 9px; align-items: center; }
  .execution-steps > div > span { width: 25px; height: 25px; z-index: 1; display: grid; place-items: center; border: 1px solid #d9e0d9; border-radius: 9px; background: #f5f6f2; color: #89928e; }
  .execution-steps .complete > span { border-color: #c9e7d9; background: #e5f6ed; color: #17684e; }
  .execution-steps .active > span { border-color: #6acfa7; background: #70d7b0; color: #164838; box-shadow: 0 0 0 5px rgba(112,215,176,.12); }
  .execution-steps strong, .execution-steps small { display: block; }
  .execution-steps strong { font-size: .64rem; }
  .execution-steps small { margin-top: 2px; color: #87918d; font-size: .55rem; }
  .asset-result { margin-top: 16px; padding: 9px; display: grid; grid-template-columns: 65px 1fr; gap: 10px; align-items: center; border: 1px solid #dce5dd; border-radius: 13px; background: #f6f8f4; }
  .asset-art { aspect-ratio: 4/3; display: grid; place-content: center; border-radius: 9px; background: radial-gradient(circle at 70% 20%, #ffe8af, transparent 28%), linear-gradient(140deg, #8be1bd, #dff6ed); color: #184c3a; text-align: center; }
  .asset-art span { font-size: .44rem; letter-spacing: .15em; }
  .asset-art strong { font-size: .75rem; letter-spacing: -.02em; }
  .asset-result > div:last-child small, .asset-result > div:last-child strong, .asset-result > div:last-child span { display: block; }
  .asset-result > div:last-child small { color: #89928e; font-size: .47rem; letter-spacing: .08em; }
  .asset-result > div:last-child strong { margin-top: 3px; font-size: .64rem; }
  .asset-result > div:last-child span { margin-top: 6px; color: #277057; font-size: .54rem; }

  .ecosystem { min-height: 100px; padding: 22px max(24px, calc((100vw - 1240px) / 2)); display: flex; align-items: center; justify-content: space-between; gap: 30px; border-block: 1px solid #e0e5de; background: rgba(248,249,245,.75); }
  .ecosystem > span { color: #87908c; font-size: .61rem; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; }
  .ecosystem > div { display: flex; align-items: center; gap: clamp(18px, 3vw, 40px); color: #51605a; }
  .ecosystem strong { font-size: .78rem; letter-spacing: -.015em; }
  .ecosystem i { width: 4px; height: 4px; border-radius: 50%; background: #9bd9c0; }

  .landing-section { width: min(1180px, calc(100% - 36px)); margin: 0 auto; padding: 120px 0; }
  .section-heading { max-width: 760px; }
  .section-heading.centered { margin-inline: auto; text-align: center; }
  .section-kicker { color: #197358; font-size: .62rem; font-weight: 850; letter-spacing: .15em; text-transform: uppercase; }
  .section-heading h2, .closing h2 { margin: 12px 0 0; font-size: clamp(2.35rem, 4.5vw, 4.7rem); line-height: .98; letter-spacing: -.062em; font-weight: 650; text-wrap: balance; }
  .section-heading p { max-width: 700px; margin: 17px 0 0; color: #6c7872; font-size: .88rem; line-height: 1.7; }
  .centered p { margin-inline: auto; }

  .infrastructure { padding-top: 132px; }
  .partner-grid { margin-top: 46px; display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  .partner-card { position: relative; min-height: 455px; padding: 28px; overflow: hidden; border: 1px solid #d9e2da; border-radius: 25px; background: #f6faf7; box-shadow: 0 12px 38px rgba(35,52,46,.055); }
  .partner-card::after { content: ''; position: absolute; width: 300px; height: 300px; right: -145px; top: -160px; border-radius: 50%; background: rgba(112,215,176,.18); }
  .b2-card { background: #f4f9fc; }
  .b2-card::after { background: rgba(116,190,235,.17); }
  .partner-icon { width: 43px; height: 43px; display: grid; place-items: center; border: 1px solid #cce5d8; border-radius: 14px; background: #e4f6ed; color: #17684e; }
  .b2-card .partner-icon { border-color: #cde3ef; background: #e4f2fa; color: #276783; }
  .partner-number { position: absolute; top: 31px; right: 31px; color: #a0aaa5; font: .62rem Consolas, monospace; }
  .partner-card h3 { max-width: 460px; margin: 29px 0 0; font-size: 1.65rem; line-height: 1.1; letter-spacing: -.045em; }
  .partner-card p { max-width: 500px; margin: 14px 0 0; color: #6d7a74; font-size: .79rem; line-height: 1.65; }
  .modality-row { margin-top: 28px; display: flex; gap: 6px; }
  .provider-lines { margin-top: 23px; padding: 16px; display: grid; gap: 8px; border: 1px solid #dce6de; border-radius: 14px; background: rgba(255,255,255,.55); }
  .provider-lines i { height: 7px; border-radius: 99px; background: linear-gradient(90deg, #62cca3 var(--fill, 78%), #e3e9e3 var(--fill, 78%)); }
  .provider-lines i:nth-child(2) { --fill: 54%; }
  .provider-lines i:nth-child(3) { --fill: 88%; }
  .provider-lines i:nth-child(4) { --fill: 38%; }
  .object-stack { margin-top: 28px; display: grid; gap: 8px; }
  .object-stack span { min-height: 47px; padding: 10px 12px; display: flex; align-items: center; gap: 9px; border: 1px solid #d9e5eb; border-radius: 12px; background: rgba(255,255,255,.66); color: #4d5f67; font: .61rem Consolas, monospace; }
  .object-stack b { margin-left: auto; color: #2c7895; font: 800 .49rem Inter, sans-serif; letter-spacing: .06em; text-transform: uppercase; }

  .surfaces { width: 100%; padding-inline: max(18px, calc((100vw - 1180px) / 2)); background: linear-gradient(180deg, transparent, #f0f8fa 12%, #eff8f5 88%, transparent); }
  .surface-tabs { width: fit-content; margin: 37px auto 0; padding: 5px; display: flex; gap: 4px; border: 1px solid #d4ded6; border-radius: 14px; background: rgba(255,254,250,.78); box-shadow: 0 8px 24px rgba(35,52,46,.05); }
  .surface-tabs button { min-height: 39px; padding: 8px 13px; display: inline-flex; align-items: center; gap: 7px; border: 0; border-radius: 10px; background: transparent; color: #718079; cursor: pointer; font-size: .7rem; font-weight: 750; }
  .surface-tabs button[aria-selected='true'] { background: #1d3129; color: #f6fbf8; box-shadow: 0 5px 14px rgba(29,49,41,.16); }
  .surface-tabs button:active { transform: scale(.98); }
  .surface-panels { margin-top: 24px; display: grid; }
  .surface-panel { grid-area: 1 / 1; min-height: 520px; padding: 46px; display: grid; grid-template-columns: .75fr 1.25fr; gap: 48px; align-items: center; border: 1px solid #d4ded7; border-radius: 27px; background: rgba(255,254,250,.92); box-shadow: 0 20px 64px rgba(35,52,46,.09); opacity: 0; visibility: hidden; pointer-events: none; transform: translateY(7px); }
  .surface-panels[data-motion='true'] .surface-panel { transition: opacity 190ms var(--ease-out), transform 190ms var(--ease-out), visibility 0s linear 190ms; }
  .surface-panel.surface-active { opacity: 1; visibility: visible; pointer-events: auto; transform: translateY(0); transition-delay: 0s; }
  .surface-copy h3, .studio-caption h3 { margin: 10px 0 0; font-size: clamp(1.7rem, 3vw, 3.05rem); line-height: 1.02; letter-spacing: -.055em; }
  .surface-copy p, .studio-caption p { margin: 15px 0 0; color: #68766f; font-size: .8rem; line-height: 1.67; }
  .surface-copy ul { margin: 20px 0 0; padding: 0; display: grid; gap: 9px; list-style: none; }
  .surface-copy li { display: flex; align-items: center; gap: 8px; color: #4f6159; font-size: .7rem; }
  .surface-copy li :global(svg) { color: #27815f; }
  .surface-copy > a { margin-top: 24px; display: inline-flex; align-items: center; gap: 7px; color: #12664c; font-size: .71rem; font-weight: 800; }
  .gateway-visual { padding: 24px; display: grid; grid-template-columns: 1fr 40px .8fr; align-items: center; border: 1px solid #d8e2da; border-radius: 20px; background: #f4f8f4; }
  .gateway-code, .gateway-receipt { min-height: 255px; padding: 18px; border-radius: 15px; }
  .gateway-code { background: #1b2923; color: #dff5e9; box-shadow: 0 14px 30px rgba(19,37,29,.16); }
  .gateway-code > span { color: #80deb7; font: .61rem Consolas, monospace; }
  .gateway-code pre { margin: 19px 0 0; color: #d3e7dc; font: .62rem/1.75 Consolas, monospace; white-space: pre-wrap; }
  .gateway-arrow { display: grid; place-items: center; color: #77a38f; }
  .gateway-receipt { display: flex; flex-direction: column; align-items: center; justify-content: center; border: 1px solid #cce5d8; background: #e7f6ef; color: #226e54; text-align: center; }
  .gateway-receipt strong { margin-top: 12px; font-size: .82rem; }
  .gateway-receipt span { margin-top: 7px; color: #5e786c; font-size: .58rem; }
  .studio-panel { grid-template-columns: 1.35fr .65fr; }
  .studio-window { overflow: hidden; border: 1px solid #303933; border-radius: 18px; background: #18211d; color: #dce8e1; box-shadow: 0 22px 54px rgba(18,31,25,.2); }
  .studio-top { min-height: 42px; padding: 0 13px; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; border-bottom: 1px solid #35413b; color: #aab7b0; font-size: .55rem; }
  .studio-top > span:first-child { display: flex; align-items: center; gap: 6px; color: #d7e7df; font-weight: 750; }
  .studio-top > span:last-child { justify-self: end; }
  .studio-top div { padding: 3px; display: flex; border-radius: 8px; background: #242f2a; }
  .studio-top button { padding: 5px 8px; border: 0; border-radius: 6px; background: transparent; color: #8f9c95; font-size: .5rem; }
  .studio-top button.selected { background: #3a4942; color: white; }
  .studio-body { min-height: 365px; display: grid; grid-template-columns: 82px 1fr 92px; }
  .studio-body aside { padding: 12px 9px; border-right: 1px solid #303a35; background: #1e2823; }
  .studio-body aside.inspector { border-right: 0; border-left: 1px solid #303a35; }
  .studio-body aside > strong { display: block; margin-bottom: 10px; color: #7f8e86; font-size: .44rem; letter-spacing: .1em; }
  .media-thumb { height: 53px; margin-bottom: 7px; border: 1px solid #3b4841; border-radius: 8px; }
  .mint-thumb { background: linear-gradient(145deg, #68d4ad, #244238); }
  .blue-thumb { background: linear-gradient(145deg, #64a7d3, #202d36); }
  .dark-thumb { background: linear-gradient(145deg, #6e516e, #242028); }
  .studio-center { min-width: 0; padding: 15px; display: flex; flex-direction: column; }
  .studio-center video { width: 100%; max-height: 218px; flex: 1; object-fit: contain; border-radius: 9px; background: #0e1512; }
  .transport { min-height: 30px; display: flex; align-items: center; gap: 8px; color: #839088; font-size: .49rem; }
  .transport button { width: 23px; height: 23px; padding: 0; border: 0; border-radius: 7px; background: #324139; color: white; font-size: .5rem; }
  .transport i { height: 2px; flex: 1; background: linear-gradient(90deg, #6bd4aa 56%, #39443f 56%); }
  .mini-timeline { position: relative; padding: 7px 4px; display: grid; gap: 5px; border-top: 1px solid #303a35; }
  .track { height: 17px; display: flex; gap: 3px; }
  .track i { display: block; border-radius: 4px; background: #356f5a; }
  .video-track i { width: 33%; }
  .audio-track i { width: 82%; background: repeating-linear-gradient(90deg, #546b84 0 2px, #3d5063 2px 4px); }
  .caption-track i { width: 37%; background: #806c44; }
  .playhead { position: absolute; left: 56%; top: 0; bottom: 0; width: 1px; z-index: 2; background: #f5d56b; }
  .inspector-row { margin-top: 12px; display: flex; justify-content: space-between; color: #88958e; font-size: .46rem; }
  .inspector-row span { color: #c0cbc5; }
  .inspector i { height: 3px; margin-top: 5px; display: block; background: linear-gradient(90deg, #65cba3 70%, #3a4640 70%); }
  .console-visual { padding: 22px; border: 1px solid #dbe3dc; border-radius: 20px; background: #f6f8f4; }
  .console-stats { display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; }
  .console-stats span { padding: 14px; border: 1px solid #dde4dd; border-radius: 12px; background: white; }
  .console-stats small, .console-stats strong { display: block; }
  .console-stats small { color: #8a948f; font-size: .48rem; letter-spacing: .07em; }
  .console-stats strong { margin-top: 7px; font-size: .76rem; }
  .console-evidence { margin-top: 10px; padding: 17px; display: grid; gap: 10px; border: 1px solid #dde5de; border-radius: 13px; background: white; }
  .console-evidence > strong { font-size: .72rem; }
  .console-evidence span { display: flex; align-items: center; gap: 8px; color: #5e6c65; font-size: .59rem; }
  .console-evidence i { width: 7px; height: 7px; border-radius: 50%; background: #4dbd8d; box-shadow: 0 0 0 4px rgba(77,189,141,.1); }
  .console-evidence b { margin-left: auto; color: #237256; font-size: .47rem; letter-spacing: .05em; }

  .workflow-grid { margin-top: 44px; display: grid; grid-template-columns: repeat(4,1fr); border-block: 1px solid #dce3dc; }
  .workflow-grid article { min-height: 275px; padding: 24px; border-right: 1px solid #dce3dc; }
  .workflow-grid article:last-child { border-right: 0; }
  .workflow-grid article > span { color: #9aa49f; font: .6rem Consolas, monospace; }
  .workflow-icon { width: 39px; height: 39px; margin-top: 35px; display: grid; place-items: center; border: 1px solid #cee5d9; border-radius: 13px; background: #e9f7f0; color: #17684e; }
  .workflow-grid h3 { margin: 20px 0 0; font-size: 1.03rem; }
  .workflow-grid p { margin: 9px 0 0; color: #6e7a74; font-size: .7rem; line-height: 1.6; }

  .closing { position: relative; width: min(1180px, calc(100% - 36px)); min-height: 490px; margin: 50px auto 110px; padding: 72px; overflow: hidden; display: flex; flex-direction: column; justify-content: center; align-items: flex-start; border-radius: 30px; background: #70d7b0; color: #153d30; }
  .closing-glow { position: absolute; width: 620px; height: 620px; right: -210px; top: -310px; border-radius: 50%; background: radial-gradient(circle at 35% 65%, rgba(213,231,255,.95), rgba(224,205,255,.65) 30%, rgba(255,255,255,0) 68%); }
  .closing > *:not(.closing-glow) { position: relative; z-index: 1; }
  .closing .section-kicker { color: #275e4c; }
  .closing h2 { max-width: 850px; }
  .closing p { max-width: 650px; margin: 19px 0 0; color: #365e50; font-size: .83rem; line-height: 1.65; }
  footer { min-height: 170px; padding: 34px max(24px, calc((100vw - 1180px) / 2)); display: grid; grid-template-columns: 1fr auto; align-items: start; gap: 25px; border-top: 1px solid #dfe4de; background: #f5f7f3; }
  footer > div { display: flex; gap: 22px; }
  footer > div a { color: #63716a; font-size: .68rem; font-weight: 700; }
  footer > p { grid-column: 1/-1; margin: 10px 0 0; color: #929b97; font-size: .59rem; }

  :global([data-reveal].reveal-pending) { opacity: 0; transform: translateY(18px); transition: opacity 260ms var(--ease-out) var(--reveal-delay, 0ms), transform 260ms var(--ease-out) var(--reveal-delay, 0ms); }
  :global([data-reveal].reveal-pending.reveal-visible) { opacity: 1; transform: translateY(0); }

  @media (hover: hover) and (pointer: fine) {
    .marketing-header nav a:hover, .console-link:hover, footer a:hover { color: #12684d; }
    .marketing-button:hover { transform: translateY(-1px); box-shadow: inset 0 1px rgba(255,255,255,.42), 0 13px 29px rgba(41,151,110,.22); }
    .marketing-button.secondary:hover { border-color: #c6d3c8; background: white; box-shadow: 0 8px 20px rgba(35,52,46,.07); }
    .surface-copy > a:hover { text-decoration: underline; text-underline-offset: 3px; }
    .surface-tabs button:hover:not([aria-selected='true']) { background: #eff4ef; color: #33483f; }
  }

  @media (max-width: 1050px) {
    .marketing-header nav { display: none; }
    .hero { grid-template-columns: 1fr; gap: 50px; padding-top: 86px; }
    .hero-copy { max-width: 760px; margin-inline: auto; text-align: center; }
    .hero-eyebrow, .hero-actions, .hero-note { justify-content: center; }
    .hero-lede { margin-inline: auto; }
    .agent-hero { width: min(720px,100%); margin-inline: auto; }
    .surface-panel, .studio-panel { grid-template-columns: 1fr; }
    .studio-panel { align-items: start; }
    .workflow-grid { grid-template-columns: 1fr 1fr; }
    .workflow-grid article:nth-child(2) { border-right: 0; }
    .workflow-grid article:nth-child(-n+2) { border-bottom: 1px solid #dce3dc; }
  }

  @media (max-width: 760px) {
    .marketing-header { width: calc(100% - 24px); min-height: 62px; margin-top: 10px; }
    .marketing-brand small, .console-link { display: none; }
    .marketing-actions { margin-left: auto; }
    .marketing-button.compact { min-height: 36px; }
    .hero { width: calc(100% - 28px); min-height: auto; padding: 74px 0 62px; }
    .hero h1 { font-size: clamp(3.1rem, 15vw, 5rem); }
    .hero-actions { align-items: stretch; }
    .hero-actions .marketing-button { flex: 1 1 190px; }
    .agent-workspace { grid-template-columns: 1fr; }
    .conversation { display: none; }
    .agent-hero { transform: none; }
    .ecosystem { padding-inline: 18px; display: grid; text-align: center; }
    .ecosystem > div { justify-content: center; flex-wrap: wrap; gap: 13px 19px; }
    .landing-section { width: calc(100% - 28px); padding: 88px 0; }
    .partner-grid { grid-template-columns: 1fr; }
    .partner-card { min-height: 420px; padding: 23px; }
    .surfaces { width: 100%; padding-inline: 14px; }
    .surface-tabs { width: 100%; }
    .surface-tabs button { flex: 1; justify-content: center; padding-inline: 7px; }
    .surface-panel { min-height: 620px; padding: 24px; gap: 30px; }
    .gateway-visual { padding: 15px; grid-template-columns: 1fr; gap: 10px; }
    .gateway-arrow { transform: rotate(90deg); }
    .gateway-code, .gateway-receipt { min-height: 205px; }
    .studio-body { grid-template-columns: 62px 1fr; }
    .studio-body .inspector { display: none; }
    .studio-top { grid-template-columns: 1fr auto; }
    .studio-top > span:last-child { display: none; }
    .console-stats { grid-template-columns: 1fr; }
    .workflow-grid { grid-template-columns: 1fr; }
    .workflow-grid article { min-height: 230px; border-right: 0; border-bottom: 1px solid #dce3dc; }
    .workflow-grid article:last-child { border-bottom: 0; }
    .closing { width: calc(100% - 28px); min-height: 530px; margin-bottom: 70px; padding: 38px 25px; align-items: center; text-align: center; }
    .closing .hero-actions { justify-content: center; width: 100%; }
    footer { grid-template-columns: 1fr; }
    footer > div { flex-wrap: wrap; }
    footer > p { grid-column: auto; }
  }

  @media (max-width: 430px) {
    .marketing-brand strong { font-size: .9rem; }
    .marketing-button.compact { padding-inline: 10px; }
    .marketing-button.compact :global(svg) { display: none; }
    .hero h1 { font-size: 3.35rem; }
    .hero-accent { font-size: .95rem; }
    .window-bar { grid-template-columns: 1fr 1fr; }
    .window-bar > span:nth-child(2) { display: none; }
    .agent-workspace { min-height: 430px; padding: 11px; }
    .surface-tabs button { font-size: .62rem; }
    .surface-tabs button :global(svg) { display: none; }
    .surface-panel { padding: 21px; }
    .studio-body { min-height: 315px; grid-template-columns: 52px 1fr; }
    .studio-center { padding: 10px; }
    .studio-center video { max-height: 180px; }
    .closing h2 { font-size: 2.55rem; }
  }

  @media (prefers-reduced-motion: reduce) {
    .hero-enter { animation: none; }
    .surface-panels[data-motion='true'] .surface-panel { transition: none; }
    .marketing-button, .surface-tabs button { transition-property: background-color, border-color, color, box-shadow; }
    .marketing-button:hover, .marketing-button:active, .surface-tabs button:active { transform: none; }
    :global([data-reveal].reveal-pending) { opacity: 1; transform: none; transition: none; }
  }
</style>
