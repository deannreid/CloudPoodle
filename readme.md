# 🐩 CloudPoodle — Modular Cloud Auditing Framework

> “because every cloud deserves a good sniff.”

CloudPoodle is a modular, read-only cloud security & compliance auditor in Python.
It pulls, analyzes, and reports metadata from identity platforms starting with **Microsoft Entra (Azure AD)**, with **AWS**, **GCP**, and **Oracle Cloud** on the roadmap.

---

## ✨ Features (current)

* **Microsoft Entra (Azure AD) modules**

  * `tenant_overview` — core tenant facts, domains, branding, licensing
  * `app_credentials_expiry` — expiring app/SP secrets & certs (KPIs, buckets, “soonest” standouts)
  * `ca_policy_audit` — Conditional Access review with rich drawers & “overly permissive” notes
  * `pim_role_audit` — permanent vs. PIM active/eligible role assignments
  * `cis_audit` — CIS dashboard (Level 1/2) from module outputs + rule packs - **Still a WIP**
  * `priv_esc_pathing` — potential escalation paths (PIM, group owners, self-GA candidates, etc.)
  * `sp_risk_audit` — service principal & app registration risk (delegated grants; `--deep` adds app role checks)
* **HTML reports**: themed, module-scoped CSS/JS, searchable tables, “View more…” pagination, drawers
* **Exports**: JSON/CSV sidecars to re-use data (and to feed CIS rules)
* **Graph client**: retries, pagination, error messages that don’t gaslight you

Planned (scoped & stubbed, not started yet):

* **AWS** — IAM overview & key hygiene
* **GCP** — org policies & IAM bindings
* **Oracle** — compartments & IAM audit

---

## 🧭 Project layout

```
CloudPoodle/
├─ core/
│  ├─ utils.py              # printing, tables, retries, exports
│  ├─ reporting.py          # HTML/CSV rendering
│  └─ …
├─ handlers/graph/
│  ├─ client.py             # Microsoft Graph client (app-only)
│  └─ graph_helpers.py      # safe select helpers, etc.
├─ modules/
│  └─ entra/
│     ├─ tenant_overview.py
│     ├─ app_credentials_expiry.py
│     ├─ ca_policy_audit.py
│     ├─ pim_role_audit.py
│     ├─ priv_esc_pathing.py
│     └─ cis_audit.py
├─ rules/
│  └─ cis/
│     └─ entra/
│        ├─ level1.json
│        └─ level2.json
└─ CloudPoodle.py           # CLI entry point
```

---

## 🔧 Usage

### General

```bash
# Entra provider + pick a scan module (based on module name in structure)
python3 CloudPoodle.py entra --scan tenant_overview

# Entra provider + all modules (Run 3 modules at once) - Warning for Graph API Limits
python3 CloudPoodle.py entra --run_all --parallel 3

# Add HTML CSV or JSON sidecar
python3 CloudPoodle.py entra --scan app_credentials_expiry --export {html/csv/json} or multiple {--export html, csv, json}

# Verbose logging
python3 CloudPoodle.py entra --scan pim_policy --debug
```

### CIS Dashboard - Work in Progress Still.

```bash
# Evaluate Level 1 or Level 2 (uses rules/cis/entra/level{1,2}.json)
python3 CloudPoodle.py entra --scan cis_audit --export {html/csv/json}
python3 CloudPoodle.py entra --scan cis_audit --cis 1 --export {html/csv/json}

```

### SP / App Registration Risk (fast vs deep)

```bash
# FAST: delegated grants via oauth2PermissionGrants + app consent
python3 CloudPoodle.py entra --scan sp_risk_audit

# DEEP: also pull appRoleAssignments & extra lookups
python3 CloudPoodle.py entra --scan sp_risk_audit --deep
```

### Privilege Escalation Pathing

```bash
python3 CloudPoodle.py entra --scan priv_esc_pathing --export {html/csv/json}
```

---

## 🧩 CLI flags (Entra)

| Flag                       | What it does                                                    |
| -------------------------- | --------------------------------------------------------------- |
| `--scan <module>`          | Which module to run (see list above)                            |
| `--html <file>`            | Write standalone HTML report                                    |
| `--export <path>`          | Export CSV + JSON (module-specific)                             |
| `--debug`                  | Extra logging                                                   |
| `--cis {1\|2}`             | CIS level for `cis_audit`                                       |
| `--deep`                   | Extra depth for modules that support it (e.g., `sp_risk_audit`) |

---

## 🔐 Auth & permissions (Entra)

App-only (client credentials). Set environment variables or you’ll be prompted:

| Env var                     | Purpose         |
| --------------------------- | --------------- |
| `CLOUDPOODLE_TENANT_ID`     | Entra tenant    |
| `CLOUDPOODLE_CLIENT_ID`     | App (client) ID |
| `CLOUDPOODLE_CLIENT_SECRET` | Client secret   |

Recommended **application** permissions (read-only) for best coverage:

* `Directory.Read.All`
* `User.Read.All`
* `Group.Read.All`
* `Application.Read.All`
* `RoleManagement.Read.Directory`
* `Policy.Read.All` *(for CA policy details & security defaults)*

> Missing permissions are handled gracefully—modules degrade or skip specific calls with warnings.

---

## 📁 Reports

By default, you choose output paths via `--export`.
Your environment or wrapper will place them under e.g. `~/.cloudpoodle/reports/<timestamp>/<module>/`.

Each HTML has:

* compact table toolbars (search + optional “View more…”)
* sticky headers, wrapped JSON
* drawers for rich object details
* top KPIs + small charts

---

## 🧪 CIS rules format

Rules live here:

```
rules/cis/{provider}/level1.json
rules/cis/{provider}/level2.json
```

They’re provider-scoped JSON files (id, title, severity, source.module/path, operator/value, remediation, tags…).
You can edit these without touching code; `cis_audit` just re-evaluates them against module payloads.

---

## 🗺 Roadmap

* AWS: IAM overview, key hygiene (read-only)
* GCP: org policy & IAM bindings
* Oracle: IAM & compartments
* Multi-provider combined dashboards
* Optional PDF export

---

## 🧠 Dev notes

* A module only needs:

  ```python
  def run(client, args): ...
  ```
* Module payloads return a dict: keys become report sections.
  Special keys: `"_kpis"`, `"_charts"`, `"_standouts"`, `"_inline_css"`, `"_inline_js"`, `"_container_class"`, `"_title"`, `"_subtitle"`.
* Tables are built from `list[dict]` values. Dict/list values inside rows are auto-rendered as pretty JSON dropdowns.

---

questions / bugs? open an issue with the module name + the `[∆]`/`[✗]` log lines you see—those messages are designed to make triage easy.
