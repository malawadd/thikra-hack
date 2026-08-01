# Human buyer demo

Run `pnpm demo:human`, open `/services`, select the flagship offer, inspect its human/JSON-Schema views, request the deterministic quote, and create an order. Continue on `/orders/{publicOrderNumber}` through bounded authorization, clearly simulated demo approval, paid-only fulfillment, controlled retry, verified delivery, receipt inspection, acceptance or dispute.

The SvelteKit UI calls the public `/api/v1` contract through its same-origin BFF. The demo API key is injected server-side and is never bundled into browser code.
