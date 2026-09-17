# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities privately to
**opensource@oneprocloud.com**.

Do not open a public GitHub issue for a security problem — a public report
exposes users who have not upgraded yet.

Include whatever you have:

- What the issue is and which component it affects
- Steps to reproduce, or a proof of concept
- The SourceLens version and how it was installed
- Any impact you have already established

You will get an acknowledgement within 3 business days. We will tell you
whether we consider the report a vulnerability, and if so, give you an
estimated fix timeline. We will credit you in the release notes unless you
ask us not to.

## Supported Versions

Security fixes land on the latest release. Upgrade with the same one-command
installer used for the original install — see the
[Quick Start](README.md#-quick-start).

## Scope

In scope:

- The SourceLens backend, frontend, LensNode worker, and the installer
- Authentication, authorization, and assistant access boundaries
- Data source credential handling
- Anything that lets one tenant or user reach another's data

Out of scope:

- Findings that require an already-compromised host or administrator account
- Vulnerabilities in third-party models or services SourceLens connects to —
  report those to the vendor
- Missing hardening that is documented as a deliberate trade-off (see below)

## Known Trade-offs

LensNode runs agent commands with the `trusted_container` execution backend by
default. Commands execute inside the LensNode container with no command
blacklist. This is a deliberate design choice, not an additional sandbox: do
not expose a LensNode container to untrusted users. Set
`LENSNODE_EXECUTION_BACKEND=filesystem` to restrict the agent to file
operations only.

Reports that amount to "the trusted backend can run commands" are expected
behavior. Reports that a user can reach a LensNode they were never granted
access to are in scope.
