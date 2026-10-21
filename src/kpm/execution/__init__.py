"""Live execution for Kalshi (Trade API v2). Watch-only (dry-run) unless every live gate passes.

Layout (see docs/execution_design.md):

* ``units``   exact unit conversions; ``to_book()`` is the only outcome-price -> (side, YES price) path
* ``auth``    request signing (Ed25519 / RSA-PSS), WebSocket signing path
* ``env``     KALSHI_ENV (demo|prod, no default), the allowlisted host table, per-environment keys
* ``client``  ExecClient: endpoint allowlist, no redirects, read retries, never-resend writes, clock-skew probe
* ``ledger``, ``safety``, ``reconcile``, ``botstate``: ledger, caps and gates, reconciliation, dashboard state
* ``late_favorite``, ``pair_h2``: the two strategies (watch-only unless every live gate passes)

Research code (``kpm.analysis``, ``kpm.collectors``, ``kpm.clients``) never imports this package.
Submodules are not imported here so that importing one never pulls in the others.
"""
