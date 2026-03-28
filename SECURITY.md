# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

Only the latest release in the `0.1.x` series receives security fixes.

## Reporting a Vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities by email to **security@icvdjango.example.com** (replace
with the actual maintainer address before publication). Include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce or a minimal proof-of-concept.
- The icv-tree version you tested against.
- Any suggested remediation if you have one.

You will receive an acknowledgement within **3 business days**. We aim to
provide an initial assessment within **7 days** of receipt.

## Disclosure Policy

icv-tree follows **coordinated disclosure** with a **90-day embargo** period:

1. Vulnerability is reported privately to the maintainers.
2. Maintainers assess severity and develop a fix.
3. A patched release is published.
4. A security advisory is issued at the same time as the release.
5. If no fix is available after 90 days, the reporter may disclose publicly.

We will credit reporters in the advisory unless anonymity is requested.

## Known Attack Surface

icv-tree is a pure library package. Its attack surface is narrow by design:

- **No views or URL routing** — icv-tree registers no HTTP endpoints.
  Consuming projects are responsible for all access control.
- **No user input handled directly** — all write paths accept model
  instances and Python values from the calling application layer.
  Input validation is the responsibility of the consuming project's
  forms, serialisers, or service layer.
- **No network I/O** — icv-tree makes no outbound connections.
- **No file I/O** — icv-tree does not read from or write to the filesystem.
- **Template tags** — ``{% recurse_tree %}`` and ``{% tree_breadcrumbs %}``
  do not call ``mark_safe`` anywhere. All output is subject to Django's
  standard auto-escaping. Node attribute values that may contain user
  content are escaped by the template engine before rendering.

### CTE raw SQL (opt-in, PostgreSQL only)

The ``_rebuild_cte`` function in ``services/integrity.py`` executes a
recursive CTE query using an f-string. The interpolated values are:

| Value | Source | Safe? |
|-------|--------|-------|
| Table name | ``model._meta.db_table`` | Django model registry |
| PK column | ``model._meta.pk.column`` | Django model registry |
| ``step_length`` | ``ICV_TREE_STEP_LENGTH`` setting | Integer from Django settings |
| ``separator`` | ``ICV_TREE_PATH_SEPARATOR`` setting | String from Django settings |

None of these values come from HTTP request data or end-user input.
As a defence-in-depth measure, the table name and column name are validated
against ``^[A-Za-z0-9_]+$`` and quoted with ``connection.ops.quote_name()``
before interpolation.

This opt-in path is only active when **both** ``ICV_TREE_ENABLE_CTE = True``
in Django settings **and** the database backend is PostgreSQL.
