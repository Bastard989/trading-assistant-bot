# Changelog

## Unreleased

- Added Telegram Mini App signature verification and personal owner allowlist.
- Removed URL user identity and protected all attachment/media reads.
- Added safe image decode/re-encode, CSP, rate limits, and stable external-error mapping.
- Added atomic trade transitions and durable HTTP/Telegram idempotency.
- Added transactional checksum migrations, indexes, FK enforcement, backup/restore tooling, health endpoints, tests, and CI.
- Replaced probability language with an explicitly heuristic rule score.
- Replaced public-price auto-close with deduplicated, retryable level observations requiring manual execution confirmation.
