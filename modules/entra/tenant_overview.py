# ================================================================
# File     : tenant_overview.py
# Purpose  : Retrieve and summarise core Entra tenant information
# Notes    : Read-only Graph. Robust to schema differences. KV tables
#            in console; compact branding; safe fallbacks on 400s.
# ================================================================

from datetime import datetime, timezone
from typing import Dict, Any, List

from core.utils import (
    fncPrintMessage,
    fncToTable,
    fncExportCSV,
    fncWriteJSON,
    fncNewRunId,
)
from core.reporting import fncWriteHTMLReport
from handlers.graph.graph_helpers import safe_select_get_all  # still used for org

REQUIRED_PERMS = ["Directory.Read.All"]  # extras: Policy.Read.All, Reports.Read.All


# ------------------------- helpers -------------------------

def _to_str(val):
    if isinstance(val, list):
        return ", ".join(map(str, val))
    if isinstance(val, dict):
        return "; ".join(f"{k}={v}" for k, v in val.items())
    return val

def _kv_rows(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"Field": k, "Value": d.get(k)} for k in d.keys()]

def _get_domains_relaxed(client) -> List[Dict[str, Any]]:
    """
    Try domains with a fuller $select first. If Graph returns any error,
    retry without known-problematic fields (e.g., isRootDomain) and
    inject 'Not Found' for those fields.
    """
    full = ["id", "isVerified", "isDefault", "isRootDomain", "authenticationType"]
    try:
        # try the full set (fast path)
        items = client.get_all(f"domains?$select={','.join(full)}")
        return items
    except Exception as ex:
        fncPrintMessage(
            "Domain property not supported by this API/tenant — "
            "retrying without 'isRootDomain'.", "warn"
        )
        minimal = [f for f in full if f != "isRootDomain"]
        try:
            items = client.get_all(f"domains?$select={','.join(minimal)}")
        except Exception as ex2:
            # absolute fallback: fetch without $select at all
            fncPrintMessage(
                f"Retry without $select also failed ({ex2}); falling back to full domains listing.",
                "warn"
            )
            items = client.get_all("domains")

        # make sure the missing field is present as "Not Found"
        for it in items:
            it.setdefault("isRootDomain", "Not Found")
        return items


# --------------------------- run ----------------------------

def run(client, args):
    run_id = fncNewRunId("tenant")
    ts = datetime.now(timezone.utc).isoformat()
    fncPrintMessage(f"Running Tenant Overview (run={run_id})", "info")

    # ---------- Organization (safe select) ----------
    org_fields = [
        "id", "displayName", "verifiedDomains", "onPremisesSyncEnabled",
        "createdDateTime", "privacyProfile", "countryLetterCode",
    ]
    try:
        org_list, org_missing = safe_select_get_all(client, "organization", org_fields)
        org = org_list[0] if org_list else {}
        for f in org_missing:
            org.setdefault(f, "Not Found")
        if org_missing:
            fncPrintMessage(f"Organization fields not found: {', '.join(org_missing)}", "warn")
    except Exception as ex:
        fncPrintMessage(f"Failed to fetch organisation: {ex}", "error")
        return {"error": str(ex)}

    # ---------- Domains (relaxed) ----------
    try:
        domains = _get_domains_relaxed(client)
    except Exception as ex:
        fncPrintMessage(f"Failed to fetch domains: {ex}", "error")
        domains = []

    verified_count = sum(1 for d in domains if str(d.get("isVerified")) == "True")
    federated_count = sum(1 for d in domains if str(d.get("authenticationType")).lower() == "federated")

    # ---------- Branding (singleton) ----------
    branding = []
    branding_kv = []
    org_id = org.get("id")
    if org_id:
        try:
            b = client.get(f"organization/{org_id}/branding")
            if isinstance(b, dict):
                branding_row = {
                    "backgroundColor": b.get("backgroundColor"),
                    "signInPageText": (b.get("signInPageText") or ""),
                    "bannerLogoUrl": b.get("bannerLogoRelativeUrl") or b.get("bannerLogo"),
                    "backgroundImageUrl": b.get("backgroundImageRelativeUrl") or b.get("backgroundImage"),
                    "cdnList": _to_str(b.get("cdnList") or []),
                    "customResetUrl": b.get("customAccountResetCredentialsUrl"),
                    "customCannotAccessUrl": b.get("customCannotAccessYourAccountUrl"),
                    "squareLogoUrl": b.get("squareLogoRelativeUrl") or b.get("squareLogo"),
                }
                branding = [branding_row]
                branding_kv = _kv_rows(branding_row)
        except Exception as ex:
            fncPrintMessage(f"Branding not available: {ex}", "warn")
    else:
        fncPrintMessage("No organisation ID available to query branding.", "warn")

    branding_configured = bool(branding and any(v for k, v in branding[0].items() if k not in ("cdnList",)))

    # ---------- Security Defaults (best-effort, tolerant) ----------
    sec_defaults_enabled = "Unknown"
    try:
        sd = client.get("policies/identitySecurityDefaults") or {}
        if isinstance(sd, dict) and "isEnabled" in sd:
            sec_defaults_enabled = sd.get("isEnabled", "Unknown")
    except Exception as ex:
        fncPrintMessage("Security Defaults policy not accessible (Policy.Read.All or endpoint not available).", "warn")

    # ---------- Authorization Policy (best-effort) ----------
    authz_policy = {}
    default_user_role_summary = {}
    try:
        authz_policy = client.get("policies/authorizationPolicy") or {}
        durp = (authz_policy.get("defaultUserRolePermissions") or {}) if isinstance(authz_policy, dict) else {}
        default_user_role_summary = {
            "canCreateApps": durp.get("allowedToCreateApps", "Unknown"),
            "canCreateSecurityGroups": durp.get("allowedToCreateSecurityGroups", "Unknown"),
            "canReadOtherUsers": durp.get("allowedToReadOtherUsers", "Unknown"),
            "canAddGuests": durp.get("allowedToInviteGuests", "Unknown"),
            "canReadBitlockerKeys": durp.get("allowedToReadBitlockerKeysForOwnedDevice", "Unknown"),
        }
    except Exception as ex:
        fncPrintMessage("Authorization policy not accessible.", "warn")

    # ---------- Licensing ----------
    licenses = []
    try:
        licenses = client.get_all("subscribedSkus")
    except Exception as ex:
        fncPrintMessage(f"Subscribed SKUs not accessible: {ex}", "warn")

    lic_rows = []
    total_skus = total_enabled = total_consumed = 0
    for s in licenses or []:
        total_skus += 1
        consumed = int(s.get("consumedUnits", 0) or 0)
        enabled = int((s.get("prepaidUnits") or {}).get("enabled", 0) or 0)
        total_consumed += consumed
        total_enabled += enabled
        lic_rows.append({
            "skuPartNumber": s.get("skuPartNumber", "Unknown"),
            "capabilityStatus": s.get("capabilityStatus", "Unknown"),
            "enabled": enabled,
            "consumed": consumed,
        })

    # ---------- Summary ----------
    default_domain = next((d.get("id") for d in domains if d.get("isDefault") is True), "N/A")
    summary = {
        "Tenant Name": org.get("displayName", "Unknown"),
        "Tenant ID": org.get("id", "Unknown"),
        "Country/Region": org.get("countryLetterCode", "N/A"),
        "Created": org.get("createdDateTime", "N/A"),
        "On-prem Sync Enabled": org.get("onPremisesSyncEnabled", False),
        "Security Defaults Enabled": sec_defaults_enabled,
        "Branding Configured": branding_configured,
        "Default Domain": default_domain,
        "Total Domains": len(domains),
        "Verified Domains": verified_count,
        "Federated Domains": federated_count,
        "License SKUs": total_skus,
        "License Units (Enabled)": total_enabled,
        "License Units (Consumed)": total_consumed,
    }

    # ---------- Console output (KV to avoid squashing) ----------
    fncPrintMessage("Tenant Overview Summary", "info")
    print(fncToTable(_kv_rows(summary), headers=["Field", "Value"], max_rows=9999))

    if domains:
        fncPrintMessage("Domains", "info")
        print(fncToTable(domains, headers=["id", "isVerified", "isDefault", "authenticationType"], max_rows=25))
        if len(domains) > 25:
            fncPrintMessage(f"Showing first 25 of {len(domains)} domains (export for full list).", "warn")

    if branding_kv:
        fncPrintMessage("Branding", "info")
        print(fncToTable(branding_kv, headers=["Field", "Value"], max_rows=9999))

    if lic_rows:
        fncPrintMessage("Licenses (Subscribed SKUs)", "info")
        print(fncToTable(lic_rows, headers=["skuPartNumber", "capabilityStatus", "enabled", "consumed"], max_rows=20))
        if len(lic_rows) > 20:
            fncPrintMessage(f"Showing first 20 of {len(lic_rows)} SKUs (export for full list).", "warn")

    # ---------- Export payload ----------
    export_data = {
        "run_id": run_id,
        "timestamp": ts,
        "provider": "entra",
        "summary": summary,
        "domains": domains,
        "brandingKV": branding_kv,
        "authorizationPolicy": authz_policy,
        "defaultUserRole": default_user_role_summary,
        "licenses": lic_rows,
    }

    # Legacy per-module export support (kept just in case)
    if getattr(args, "export", None) and isinstance(args.export, str):
        csv_path = args.export
        fncExportCSV(csv_path, domains)
        fncWriteJSON(csv_path + ".json", export_data)
        fncPrintMessage(f"Exported CSV → {csv_path} and JSON → {csv_path}.json", "success")

    if getattr(args, "html", None) and isinstance(args.html, str):
        html_path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(html_path, "tenant_overview", export_data)

    fncPrintMessage("Tenant Overview module complete.", "success")
    return export_data
