# Security Policy

## Reporting a Vulnerability

If you discover a potential security vulnerability in this package, please do not open a public issue. Instead, send an email to [madgagarin@gmail.com].

We take security seriously and will respond to your report as soon as possible.

## Security Model

This package adheres to the **HLN (Hierarchical Logic Network) Security Model**, which emphasizes:
- **Zero Trust Architecture**
- **Mutual TLS (mTLS)** for all connections.
- **Short-lived Access Tokens** via STS.
- **No Inbound Ports** for workers (PULL model).

For a detailed explanation of our security principles, please refer to the [HLN Security Specification](../../packages/hln/SECURITY.md).
