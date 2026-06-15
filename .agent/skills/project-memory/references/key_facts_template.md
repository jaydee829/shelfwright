# Key Project Facts

This file tracks important project configuration, constants, and environment details.

## Project Overview
- **Project Name**: <name>
- **Description**: <one-line description of what the project does>

## Local Development
- **OS / Runtime**: <host OS, container/VM, language version>
- **Primary Workflow**: <how the project is run day-to-day — IDE, container, scripts>
- **Setup**: <the minimal steps to a working dev environment>

## Technology Stack
- **Database**: <engine + notable extensions/versions>
- **Backend / Interface**: <frameworks>
- **Key Libraries / Protocols**: <the load-bearing ones>
- **Testing**: <unit / integration / e2e tooling>
- **Containerization**: <Docker / Compose, exposed ports>

## Production / Cloud
- **Project / Account IDs**: <cloud project ids, regions>
- **Service URLs**: <deployed endpoints>
- **Service Accounts (names only)**: <SA emails / roles — names, not keys>
- **Monitoring / Dashboards**: <links>

## Usage Tips
- Organize facts by category; prefer bullet lists over tables for easy editing.
- Include both production and development details, and add URLs for navigation.
- Prefer documented facts here over assumptions when looking up config.

## SECURITY — What NOT to Store

This file is committed to version control. **Never** put secrets here:

- ❌ Passwords, API keys, tokens, private keys, connection strings with embedded credentials
- ❌ `.env` file contents, OAuth client secrets, signing keys, certificates
- ❌ Anything you would not paste into a public PR

Instead, store:

- ✅ The **name/location** of a secret and how to obtain it
  (e.g., "DB password lives in `.env` as `DB_PASSWORD`; prod value in GCP Secret Manager
  secret `librarian-db-url`").
- ✅ Service account **emails** and **roles** (identifiers, not key material).
- ✅ Non-secret config: ports, hostnames, region names, public URLs, project IDs.

If a secret ever lands in this file, treat it as compromised: rotate it and scrub history.
