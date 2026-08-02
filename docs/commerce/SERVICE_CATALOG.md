# Service catalog

Thikra publishes immutable `ServiceOffer` versions. Six version-1 offers are
seeded in every environment: `arabic-product-image`, `product-video-15s`,
`arabic-voice-over`, `verified-vertical-ad`, `media-compliance-check`, and
`provenance-package`. Local `SANDBOX` additionally seeds
`sandbox-video-smoke-test`: a fixed 5-second OpenAI-image-to-GMI-Cloud-video
path priced below the local USD 5 test-fulfillment cap.

Every offer carries Draft 2020-12 input/output JSON Schemas, integer-minor-unit price boundaries, delivery estimates, retry ceilings, verification checks, human-review availability, provider policy, and commercial-use terms. The flagship `verified-vertical-ad` is capped at USD 10.00. Published versions cannot be edited after an order references them; operator changes create a new version.

Public catalog: `GET /api/v1/services`, `GET /api/v1/services/{slug}`, and `/.well-known/thikra-services.json`.
