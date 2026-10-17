# Security

## Report a vulnerability

Please report security issues privately instead of opening a public issue. On GitHub, open the repository's
**Security** tab and choose **Report a vulnerability** to submit a private report. The `adotak` repository owner
must enable private vulnerability reporting in **Settings → Code security** before this option is available.

The only supported version is the latest code on `main`.

## Security model

- When Clerk is configured, session JWTs are checked against the instance's signing keys and expected issuer, and
  the authorized-party claim is checked when `CLERK_AUTHORIZED_PARTIES` is configured.
- Vehicle routes check that the signed-in user owns the vehicle. Vehicle ingest keys are stored as SHA-256 hashes.
- Request limits are stored in the database so they are shared between application instances.
- Content security policy and other security headers help restrict browser resources and unsafe framing.
- The optional scheduled cleanup endpoint requires the `CRON_SECRET` bearer token.
- Secrets belong in deployment environment variables or local environment files; no secrets should be committed to
  the repository.
