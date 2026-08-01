# Noura Glow demo script

1. Set `APP_MODE=DEMO`, run `pnpm dev`, and open `http://localhost:43191`. Point out the persistent DEMO label: no real payment or provider generation is claimed.
2. Open **New Brief**. The Noura Glow brief requests three 15-second vertical Arabic advertisements under USD 20, no real likenesses or medical claims, at most two retries, and final human approval.
3. Compile. Review the human-readable mandate and JSON inspector; lower or edit a material constraint to demonstrate a new version, then confirm.
4. Review provider quotes. Explain the transparent quality, reliability, latency, configuration, compliance, and estimated-cost score. Pick providers per modality if desired.
5. Review the exact bounded action and authorize. The demo gateway creates a simulated authorization record; authorization is explicitly not settlement.
6. Launch. On the run page, edit a scene prompt or Arabic narration and save it. Confirm the storyboard and start.
7. Watch stable SSE events and progressive scenes. The controlled fixture intentionally omits scene 2 narration; verification reports a deterministic FAIL plus a commercial-rights warning and required human review.
8. Retry the failed narration. The backend enforces retry count and budget and changes the failed check to PASS while preserving the original event history.
9. Approve final delivery. Open/download the final fixture and inspect its B2-compatible asset metadata, hash, parent links, manifest key, payment reference, and verification state.
10. Open **Payments** to show authorization and invoked amounts separately. Open **Evidence**, toggle Graph, inspect hashes, and export JSON.
11. Open the seeded case, add a note, assign it, resolve it, and export evidence. Explain that redress is not labeled a refund because Prava's official skill documents no refund API.

Expected terminal state: completed and accepted after one retry and explicit principal approval. The audit chain remains valid.
