# Genblaze fulfillment adapter

A paid commercial order is compiled through the existing `BriefCreate → CreativeMandate → provider_strategy → launch_run` services. The new `FulfillmentJob` links the immutable commercial order to the existing mandate and `GenerationRun`; pipelines, provider catalog boundaries, preflight policy, and composer rules are unchanged.

Required/forbidden providers and retry ceilings flow into the mandate. Payment must exactly match the quote before the job exists. Generation failure, verification uncertainty, and exhausted retry budget remain explicit states and cases. Demo materializes the existing controlled fixture, records one verification failure, then exercises the normal retry policy without corrupting provider output.
