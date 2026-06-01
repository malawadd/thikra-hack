# Infra notes

## Backblaze B2 bucket

1. Create a bucket (private). Note the region from the bucket detail —
   it's the value that goes into `B2_REGION` (e.g. `us-west-004`).
2. Create an Application Key scoped to the bucket with `listFiles`,
   `readFiles`, `writeFiles`, `deleteFiles` permissions. Drop the
   `keyID` + `applicationKey` into your `.env` as `B2_KEY_ID` +
   `B2_APPLICATION_KEY`.
3. The endpoint follows the region
   (`https://s3.<region>.backblazeb2.com`); `genblaze-s3` derives it
   from `B2_REGION` internally, so `.env` has no `B2_ENDPOINT` field.

The sample writes everything under `explainers/<run-id>/`. A storage
lifecycle rule with a 30-day expiry on that prefix keeps cost capped
while leaving recent runs browsable from the per-run asset list.

`S3StorageBackend.for_backblaze(..., auto_lifecycle=True)` will create
sensible defaults the first time the bucket is touched if the lifecycle
hasn't been configured manually.

## ffmpeg

The composer shells out to the system `ffmpeg` binary. Install it
before running the backend:

```bash
# macOS
brew install ffmpeg

# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg

# Docker (suggested base layer if you containerize the backend)
# Add to your Dockerfile:
#   RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
#       && rm -rf /var/lib/apt/lists/*
```

`composer.py` checks `shutil.which("ffmpeg")` before any pipeline IO; if
the binary is missing, Stage C fails immediately with a hint pointing
back here.

## CORS

The frontend talks to the backend through `/api/proxy/...`, so the
backend's CORS config only needs to allow the Next dev origin
(`http://localhost:3000` by default). Set `API_CORS_ORIGINS` in `.env`
if you deploy them behind different hostnames.
