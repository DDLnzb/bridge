# Security Policy

## Reporting a Vulnerability

Please **do not** open a public issue for a security vulnerability.

Report it privately via GitHub's vulnerability reporting on this repository
(**Security** → **Report a vulnerability**), or by mail to the maintainer.

Include:

- what the vulnerability allows an attacker to do
- the steps to reproduce it
- the affected version (image tag or commit)

You will get an acknowledgement. If the report is accepted, a fix is prepared
before disclosure, and you are credited in the advisory unless you prefer
otherwise.

## Scope

This repository contains the **bridge** — a local download-client service that
speaks the SABnzbd API and talks to your local JDownloader instance.

Not in scope of this repository:

- the hosted DDLnzb API and web portal (a separate, private service)
- the DDL portals the indexer scrapes
- JDownloader itself

## Handling credentials

The bridge needs credentials for two things:

- a DDLnzb API key — created in your own account under **API Keys**
- your JDownloader account (email, password, app key)

Both are configured through the bridge's web UI at `http://<host>:6789/ui` or via
environment variables. **Never commit them.** `.env` is listed in `.gitignore` for
exactly that reason, and the settings file written by the web UI lives in the
mounted data directory, not in the repository.

The bridge talks to the DDLnzb API only — it does not accept inbound connections
from the internet unless you expose the port yourself.
