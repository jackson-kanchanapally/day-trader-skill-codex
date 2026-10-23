# Security

This is experimental, local research software. No supported security-release window, independent security audit or hosted-service hardening is promised. Live trading transport is absent; this does not eliminate risks from local files, external content or dependencies.

The dashboard binds to loopback and is read-only over HTTP. It has no application authentication and should not be exposed to the internet or shared networks through port forwarding. Keep session folders, snapshots and paper portfolio data private. Fetched filings, news and artifact strings are untrusted data, not instructions.

Never submit tokens, private keys, account information or private trading reports in public issues or pull requests. If the repository has GitHub private vulnerability reporting enabled, use **Security → Report a vulnerability**. If it is unavailable, request a private contact channel without publishing exploit details or secrets. This repository does not assert that private reporting is already enabled and does not publish an unverified maintainer email.

If a credential has been committed, revoke/rotate it at its issuer first. Removing the working-tree file or adding an ignore rule does not remove it from Git history, forks or previously published copies. Coordinate any history cleanup with the repository owner.

When reporting an issue privately, include the affected commit, minimal reproduction using synthetic inputs, expected impact and suggested mitigation if available.
