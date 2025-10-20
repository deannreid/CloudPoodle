# ================================================================
# File     : modules/tenant_overview.py
# Purpose  : Retrieve and summarise core Entra tenant information
# Notes    : Read-only Graph. Robust to schema differences. KV tables
#            in console & HTML; module-scoped CSS/JS for light UX.
#            Updated for CloudPoodle dashboard (KPIs, standouts, charts)
# ================================================================

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

from core.utils import (
    fncPrintMessage,
    fncToTable,
    fncExportCSV,
    fncWriteJSON,
    fncNewRunId,
)
from core.reporting import fncWriteHTMLReport
from handlers.graph.graph_helpers import safe_select_get_all  # used for org

REQUIRED_PERMS = ["Directory.Read.All"]  # extras: Policy.Read.All, Reports.Read.All

# ------------------------- module-scoped CSS/JS -------------------------

TENANT_OVERVIEW_CSS = r"""
/* Scope to this module only */
.tenant-overview .card table{ table-layout:auto }

/* Toolbars */
.tenant-overview .to-toolbar{
  display:flex; gap:10px; align-items:center; margin:6px 2px 0 2px; flex-wrap:wrap;
}
.tenant-overview .to-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:220px; outline:none;
}
.tenant-overview .to-toolbar .btn{
  padding:6px 10px; border:1px solid var(--border); border-radius:999px;
  background:var(--card); cursor:pointer; font-weight:600;
}
.tenant-overview .to-toolbar .btn.active{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}

/* Compact KV look */
.tenant-overview table.summary td{ font-weight:600 }

/* Sticky headers for long lists */
.tenant-overview table thead th{ position:sticky; top:0; z-index:2 }

/* Wrap long values in JSON/KV to avoid overflow */
.tenant-overview .cp-json,
.tenant-overview .cp-json pre,
.tenant-overview .cp-json code{
  white-space: pre-wrap; word-break: break-word; overflow-x: hidden;
}
"""

TENANT_OVERVIEW_JS = r"""
(function(){
  const root = document.querySelector('.tenant-overview') || document;

  function enhanceSearchable(title){
    const slug = title.toLowerCase().replace(/[^a-z0-9]+/g,'-');
    const card = root.querySelector('#tbl-'+slug)?.closest('.card');
    const tbl  = root.querySelector('#tbl-'+slug);
    if(!card || !tbl) return;

    const bar = document.createElement('div');
    bar.className = 'to-toolbar';
    bar.innerHTML = `
      <input type="search" placeholder="Search ${title}…" aria-label="Search ${title}">
    `;
    card.insertBefore(bar, card.querySelector('.tablewrap'));
    const search = bar.querySelector('input[type="search"]');

    function apply(){
      const q = (search.value||'').toLowerCase();
      Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr=>{
        const txt = tr.textContent.toLowerCase();
        tr.style.display = (!q || txt.includes(q)) ? '' : 'none';
      });
    }
    search.addEventListener('input', apply);
  }

  enhanceSearchable('Domains');
  enhanceSearchable('Licenses');
})();
"""

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
    Query domains using only v1.0-stable properties to avoid 400s.
    """
    select = ["id", "isVerified", "isDefault", "authenticationType"]
    try:
        return client.get_all(f"domains?$select={','.join(select)}")
    except Exception as ex:
        fncPrintMessage(f"Domains $select failed ({ex}); falling back to full 'domains'.", "warn")
        return client.get_all("domains")

def _bool_to_yesno(v) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return str(v)

# ------------------------- main -------------------------

def run(client, args):
    run_id = fncNewRunId("tenant")
    ts = datetime.now(timezone.utc).isoformat()
    fncPrintMessage(f"Running Tenant Overview (run={run_id})", "info")

    # ---------- Organization ----------
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

    # ---------- Domains ----------
    try:
        domains = _get_domains_relaxed(client)
    except Exception as ex:
        fncPrintMessage(f"Failed to fetch domains: {ex}", "error")
        domains = []

    verified_count   = sum(1 for d in domains if str(d.get("isVerified")) == "True")
    federated_count  = sum(1 for d in domains if str(d.get("authenticationType") or "").lower() == "federated")
    managed_count    = sum(1 for d in domains if str(d.get("authenticationType") or "").lower() == "managed")
    unknown_count    = max(0, len(domains) - federated_count - managed_count)

    # ---------- Branding ----------
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

    # ---------- Security Defaults ----------
    sec_defaults_enabled = "Unknown"
    try:
        # Correct v1.0 endpoint:
        # GET https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy
        sd = client.get("policies/identitySecurityDefaultsEnforcementPolicy") or {}
        if isinstance(sd, dict) and "isEnabled" in sd:
            sec_defaults_enabled = sd.get("isEnabled", "Unknown")
    except Exception as ex:
        fncPrintMessage(
            f"Security Defaults policy not accessible (needs Policy.Read.All?) — {ex}",
            "warn"
        )

    # ---------- Authorization Policy ----------
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
    except Exception:
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

    # ---------- Console output ----------
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

    kpis = [
        {"label":"Total Domains","value":str(len(domains)),"tone":"primary","icon":"bi-globe2"},
        {"label":"Verified Domains","value":str(verified_count),"tone":"success","icon":"bi-patch-check"},
        {"label":"Federated Domains","value":str(federated_count),"tone":"warning","icon":"bi-diagram-3"},
        {"label":"Branding Configured","value":_bool_to_yesno(branding_configured),"tone":"info","icon":"bi-brush"},
        {"label":"Security Defaults","value":_bool_to_yesno(sec_defaults_enabled),"tone":"secondary","icon":"bi-shield-lock"},
        {"label":"License SKUs","value":str(total_skus),"tone":"primary","icon":"bi-bag-check"},
    ]
    # Add utilisation if enabled pool is present
    if total_enabled:
        kpis.append({"label":"Licenses Used","value":f"{total_consumed}/{total_enabled}","tone":"secondary","icon":"bi-graph-up"})

    standouts: Dict[str, Dict[str, Any]] = {}

    if sec_defaults_enabled is False or str(sec_defaults_enabled).lower() == "false":
        standouts["group"] = {
            "title":"Security Defaults Disabled",
            "name": org.get("displayName") or "Tenant",
            "risk_score": 9.0,
            "comment": "Consider enabling or enforcing Conditional Access"
        }

    if federated_count > 0:
        standouts["user"] = {
            "title":"Federated Identity in Use",
            "name": f"{federated_count} domain(s)",
            "risk_score": min(10.0, 6.0 + federated_count*0.5),
            "comment": "Monitor federation trust & token signing cert rollover"
        }

    dur = default_user_role_summary or {}
    risky_dur_flags = []
    if str(dur.get("canAddGuests")).lower() == "true":
        risky_dur_flags.append("Can add guests")
    if str(dur.get("canCreateApps")).lower() == "true":
        risky_dur_flags.append("Can create apps")
    if str(dur.get("canCreateSecurityGroups")).lower() == "true":
        risky_dur_flags.append("Can create security groups")
    if risky_dur_flags:
        standouts["computer"] = {
            "title":"Open Default User Role",
            "name": ", ".join(risky_dur_flags),
            "risk_score": min(10.0, 5.0 + 1.5*len(risky_dur_flags)),
            "comment": "Review authorizationPolicy.defaultUserRolePermissions"
        }

    chart_labels = ["Managed","Federated","Unknown"]
    chart_values = [managed_count, federated_count, unknown_count]
    authorization_policy_kv = _kv_rows(authz_policy) if isinstance(authz_policy, dict) else []
    default_user_role_kv    = _kv_rows(default_user_role_summary) if isinstance(default_user_role_summary, dict) else []

    export_data = {
        "run_id": run_id,
        "timestamp": ts,
        "provider": "entra",
        "summary": summary,

        "domains": domains,
        "brandingKV": branding_kv,
        "authorizationPolicyKV": authorization_policy_kv,
        "defaultUserRoleKV": default_user_role_kv,
        "licenses": lic_rows,

        "_kpis": kpis,
        "_standouts": standouts,
        "_charts": {
            "place": "summary",
            "domainTypes": {"labels": chart_labels, "data": chart_values},
        },

        "_container_class": "tenant-overview",
        "_inline_css": TENANT_OVERVIEW_CSS,
        "_inline_js":  TENANT_OVERVIEW_JS,
        "_title": "Tenant Overview",
        "_subtitle": "Core Entra tenant configuration, domains, branding and licensing",
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
