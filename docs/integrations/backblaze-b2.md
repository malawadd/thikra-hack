# Backblaze B2 integration

Media storage stays delegated to `genblaze-s3`; Thikra adds no direct AWS client. `S3StorageBackend.for_backblaze()` receives explicit `B2_REGION`, `B2_KEY_ID`, `B2_APPLICATION_KEY`, and `B2_BUCKET_NAME`; it derives the endpoint from the region.

Media assets/manifests use the existing Genblaze hierarchical sink. SQL records run/scene, type, provider/model, object key, content type, bytes, SHA-256, payment, approval, and relations. Browser access uses server-generated short-lived redirects rather than permanent public URLs.

Non-media JSON has one adapter, `app/thikra/storage.py`, using `thikra/workspaces/{workspace}/runs/{run}/evidence/evidence-export.json`. DEMO and unconfigured SANDBOX use labeled local storage; configured SANDBOX and PRODUCTION use B2.
