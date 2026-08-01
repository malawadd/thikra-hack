# Prava submission

Thikra uses Prava as a bounded authorization mechanism inside an accountable procurement workflow—not as a decorative checkout button. Mandate ID, merchant, maximum amount, currency, expiration, and exact authorized action are registered before generation. Authorization, credential readiness, invocation/reporting, delivery, verification, acceptance, and redress appear as distinct states.

The SvelteKit client uses the official secure iframe and publishable key. Secret calls, polling, one-time credentials, and outcome reporting stay in FastAPI. DEMO transactions are labeled simulated. The implementation deliberately does not invent webhooks, refunds, escrow, conditional settlement, or AI-provider card checkout capabilities absent from the official skill.
