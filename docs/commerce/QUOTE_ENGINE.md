# Deterministic quote engine

Pricing is code, not model output:

`provider estimate + verification + storage + retry reserve + platform margin + fixed fees + tax = total`.

Money is always integer minor units. `ProviderPricingAdapter` supports provider/model estimation; the default adapter uses curated static development rates and records its confidence/source. Quotes expose the full breakdown, expire after `THIKRA_QUOTE_TTL_SECONDS`, and become immutable once active. Required/forbidden providers, service price ceilings, schema constraints, currency, and the buyer's maximum budget are checked before persistence.

An accepted quote is copied exactly into the order. Cost overruns do not increase the customer charge, and fulfillment cannot relax the mandate or budget.
