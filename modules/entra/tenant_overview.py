# ================================================================
# File     : modules/tenant_overview.py
# Purpose  : Retrieve and summarise core Entra tenant information
# Notes    : Read-only Graph. Robust to schema differences. KV tables
#            in console & HTML; module-scoped CSS/JS for light UX.
#            Updated for CloudPoodle dashboard (KPIs, standouts, charts)
# ================================================================

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
from datetime import timedelta
import requests

from core.utils import (
    fncPrintMessage,
    fncToTable,
    fncNewRunId,
)
from core.reporting import fncWriteHTMLReport

REQUIRED_PERMS = [
    # Objects & policy (core)
    "Directory.Read.All",
    "Policy.Read.All",
    "RoleManagement.Read.Directory",
    # Optional enrichments
    "Report.Read.All",        # MFA registration
    "Reports.Read.All",
    "Application.Read.All",   # Apps + expiring credentials
]

# ----------------------- Module-local CSS/JS ---------------------

OVERVIEW_CSS = r"""
.toverview .kv { width:100%; border-collapse: collapse; }
.toverview .kv th,.toverview .kv td{
  text-align:left; padding:8px 12px; border-bottom:1px solid var(--border); vertical-align:top;
}
.toverview .kv th{ width:260px; white-space:nowrap; background: color-mix(in srgb, var(--accent2) 12%, var(--card)); }
.toverview .pane{
  border:1px solid var(--border); border-radius:10px;
  background: color-mix(in srgb, var(--card) 94%, #000 6%); overflow:hidden; margin-bottom:14px;
}
.toverview .pane h5{
  margin:0; padding:10px 12px; background: color-mix(in srgb, var(--accent2) 85%, #000 15%);
  color:#fff; font-weight:700; border-bottom:1px solid rgba(0,0,0,.2);
}
.toverview .flex{ display:grid; grid-template-columns: 1fr 1fr; gap:16px; }
@media (max-width:1100px){ .toverview .flex{ grid-template-columns:1fr; } }
.toverview table thead th{ position:sticky; top:0; z-index:2; }
.toverview .tight th, .toverview .tight td { white-space: nowrap; }

.toverview .btn{
  padding:6px 12px; border:1px solid var(--border); border-radius:999px;
  background:var(--card); cursor:pointer; font-weight:600;
}
.toverview .btn.primary{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}

/* Recommendations table tweaks: keep label wide, make actions narrow */
.toverview table[id*="recommend"] thead th:nth-child(1){ min-width: 280px; } /* label */
.toverview table[id*="recommend"] thead th:nth-child(4){ width: 280px; }     /* actionsText */
"""

OVERVIEW_JS = r"""
(function(){
  const root = document.querySelector('.toverview') || document;

  const blocks = [
    { id: '#tbl-domains', label: 'Domains', paginate: true, drawer: null },
    { id: '#tbl-licenses', label: 'Licences', paginate: true, drawer: 'licenses' },
    { id: '#tbl-role-summary', label: 'Directory Roles', paginate: false, drawer: null }
  ];

  function addToolbar(table, title, paginate){
    const card = table.closest('.card'); if (!card) return;
    const bar = document.createElement('div');
    bar.style.display='flex'; bar.style.gap='10px'; bar.style.margin='6px 2px';
    bar.innerHTML = `
      <input type="search" placeholder="Search ${title}…" aria-label="Search ${title}"
        style="padding:6px 10px;border-radius:999px;border:1px solid var(--border);background:var(--card);color:var(--text);min-width:220px;outline:none;">
      ${paginate ? '<button class="btn primary" data-action="viewmore" style="display:none">Show more…</button>' : ''}
    `;
    card.insertBefore(bar, card.querySelector('.tablewrap'));
    const input = bar.querySelector('input');
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    const viewMoreBtn = bar.querySelector('[data-action="viewmore"]');

    const PAGE = 20;
    let expanded = false;

    function apply(){
      const q = (input.value||'').toLowerCase();
      let shown = 0;
      rows.forEach(tr=>{
        const match = !q || tr.textContent.toLowerCase().includes(q);
        if (!match) { tr.style.display = 'none'; return; }
        if (paginate && !expanded && q === '' && shown >= PAGE) {
          tr.style.display = 'none'; return;
        }
        tr.style.display = ''; shown++;
      });
      if (paginate && viewMoreBtn){
        const hasHidden = rows.some(tr => tr.style.display === 'none') && q === '' && !expanded;
        viewMoreBtn.style.display = hasHidden ? '' : 'none';
      }
    }

    apply();
    input.addEventListener('input', apply);
    if (paginate && viewMoreBtn){
      viewMoreBtn.addEventListener('click', ()=>{ expanded = true; apply(); });
    }
  }

  function attachLicenseDrawer(table){
    const headCells = Array.from(table.querySelectorAll('thead th'));
    const headers = headCells.map(h => (h.textContent || '').trim());
    const totalCols = headCells.length;

    const detailsIdx = headers.findIndex(h => h.toLowerCase() === 'details');
    if (detailsIdx === -1) return; // nothing to expand

    Array.from(table.querySelectorAll('tbody tr')).forEach(tr=>{
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', ()=>{
        const next = tr.nextElementSibling;
        if (next && next.classList.contains('lic-expander')) { next.remove(); return; }

        const dcell = tr.children[detailsIdx];
        let details = null;
        try {
          const raw = (dcell?.textContent || '').trim();
          details = JSON.parse(raw);
        } catch(e){
          details = null;
        }

        let plansHtml = '<em>No service plans.</em>';
        if (details && Array.isArray(details.servicePlans) && details.servicePlans.length){
          plansHtml = '<div style="line-height:1.5; white-space:pre-wrap;">' +
            details.servicePlans.map(p => `• ${p}`).join('\n') +
          '</div>';
        }

        const exp = document.createElement('tr');
        const td  = document.createElement('td');
        exp.className = 'lic-expander';
        td.colSpan = totalCols;
        td.innerHTML = `
          <div style="padding:12px 14px; border-top:1px solid var(--border); background: color-mix(in srgb, var(--card) 94%, #000 6%);">
            <div style="font-weight:700; margin-bottom:6px;">Service plans for <span>${(details && (details.skuPartNumber||'')) || 'SKU'}</span></div>
            ${plansHtml}
          </div>
        `;
        exp.appendChild(td);
        tr.parentNode.insertBefore(exp, tr.nextSibling);
      });
    });

    // Hide the Details column
    const idx = detailsIdx + 1;
    table.querySelectorAll(`thead th:nth-child(${idx}), tbody td:nth-child(${idx})`)
         .forEach(c => c.style.display = 'none');
  }

  blocks.forEach(b=>{
    const table = root.querySelector(b.id);
    if (!table) return;
    addToolbar(table, b.label, !!b.paginate);
    if (b.drawer === 'licenses') attachLicenseDrawer(table);
  });
})();
"""

# ----------------------- Helpers -----------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _client_headers(client) -> Dict[str,str]:
    hdrs = getattr(client, "headers", None) or getattr(client, "_headers", None)
    if isinstance(hdrs, dict):
        if "ConsistencyLevel" not in hdrs:
            hdrs = {**hdrs, "ConsistencyLevel": "eventual"}
        return hdrs
    token = getattr(client, "token", "") or getattr(client, "_token", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "ConsistencyLevel": "eventual",
    }

def _client_handle_response(client):
    return getattr(client, "_handle_response", None) or getattr(client, "fncHandleResponse", None)

def _safe_get(obj: Dict, *path, default=None):
    cur = obj
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def _get_json(url: str, client) -> Dict[str,Any]:
    handler = _client_handle_response(client)
    resp = requests.get(url, headers=_client_headers(client))
    return handler(resp) if handler else resp.json()

def _try_get_all(client, path: str) -> List[Dict[str,Any]]:
    """Resilient client.get_all with a soft failure path."""
    try:
        return client.get_all(path)
    except Exception as ex:
        fncPrintMessage(f"get_all failed for '{path}': {ex}", "warn")
        return []

def _count_entity(client, path: str, fallback_list_path: str = None) -> int:
    """
    Try to get @odata.count in one call. If that fails, fall back to client.get_all length.
    """
    try:
        data = _get_json(f"https://graph.microsoft.com/v1.0/{path}", client)
        if isinstance(data, dict) and "@odata.count" in data:
            return int(data.get("@odata.count") or 0)
    except Exception as ex:
        fncPrintMessage(f"Count fast-path failed for {path}: {ex}", "debug")
    rows = _try_get_all(client, fallback_list_path or path.split("?$",1)[0])
    return len(rows or [])

def _get_organization(client) -> Dict[str,Any]:
    rows = _try_get_all(client, "organization?$select=id,displayName,tenantType,createdDateTime,securityComplianceNotificationMails,marketingNotificationEmails,technicalNotificationMails,privacyProfile")
    return (rows or [{}])[0]

def _get_domains(client) -> List[Dict[str,Any]]:
    """
    microsoft.graph.domain (v1.0) → use supportedServices; capabilities is invalid.
    Retry without $select if the tenant/feature flags reject it.
    """
    paths = [
        "domains?$select=id,isVerified,isDefault,isInitial,isRoot,authenticationType,rootDomain,supportedServices",
        "domains",
    ]
    for p in paths:
        rows = _try_get_all(client, p)
        if rows: return rows
    return []

def _get_subscribed_skus(client) -> List[Dict[str,Any]]:
    return _try_get_all(client, "subscribedSkus?$select=skuId,skuPartNumber,appliesTo,consumedUnits,prepaidUnits,capabilityStatus,servicePlans")

def _get_directory_roles(client) -> Tuple[int,int,Dict[str,str]]:
    """Returns (total_role_defs, custom_role_defs, name->id map)"""
    rows = _try_get_all(client, "roleManagement/directory/roleDefinitions?$select=id,displayName,isBuiltIn") or []
    total = len(rows)
    custom = sum(1 for r in rows if not r.get("isBuiltIn"))
    name_to_id = { (r.get("displayName") or ""): r.get("id") for r in rows if r.get("id") }
    return total, custom, name_to_id

def _get_ca_policies(client) -> List[Dict[str,Any]]:
    for p in ["identity/conditionalAccess/policies", "policies/conditionalAccessPolicies"]:
        rows = _try_get_all(client, p)
        if rows:
            return rows
    return []

def _get_ca_policies_count(client) -> int:
    paths = [
        "identity/conditionalAccess/policies?$count=true&$top=1",
        "policies/conditionalAccessPolicies?$count=true&$top=1",
    ]
    for p in paths:
        n = _count_entity(client, p, fallback_list_path=p.split("?$",1)[0])
        if n: return n
    return 0

def _get_auth_registration_stats(client) -> Dict[str,int]:
    """
    Optional enrichment; ignore errors if reports perms aren’t present.
    v1.0: prefer isMfaRegistered; fallback to methodsRegistered length.
    """
    out = {"registeredUsers": 0, "mfaCapableUsers": 0}
    paths = [
        "reports/authenticationMethods/userRegistrationDetails?$select=id,isMfaCapable,isMfaRegistered,methodsRegistered",
        "reports/authenticationMethods/userRegistrationDetails",
    ]
    rows: List[Dict[str,Any]] = []
    for p in paths:
        rows = _try_get_all(client, p)
        if rows: break
    if not rows:
        return out

    def _is_registered(r: Dict[str,Any]) -> bool:
        if r.get("isMfaRegistered") is not None:
            return bool(r.get("isMfaRegistered"))
        m = r.get("methodsRegistered") or []
        return len(m) > 0

    out["registeredUsers"]  = sum(1 for r in rows if _is_registered(r))
    out["mfaCapableUsers"]  = sum(1 for r in rows if bool(r.get("isMfaCapable")))
    return out

def _split_group_types(groups: List[Dict[str,Any]]) -> Tuple[int,int,int]:
    """
    Returns (security, m365, others)
    """
    security = m365 = other = 0
    for g in groups or []:
        gtypes = set((g.get("groupTypes") or []))
        if g.get("securityEnabled"):
            security += 1
        elif "Unified" in gtypes:
            m365 += 1
        else:
            other += 1
    return security, m365, other

def _summarise_licenses(skus: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    out = []
    for s in (skus or []):
        prepaid = _safe_get(s, "prepaidUnits", default={}) or {}
        enabled = int(prepaid.get("enabled") or 0)
        consumed = int(s.get("consumedUnits") or 0)
        util = round((consumed / enabled) * 100.0, 1) if enabled else 0.0
        plans = sorted({(p.get("servicePlanName") or "") for p in (s.get("servicePlans") or []) if p})
        # table shows concise scalars; details holds the full list for the drawer
        out.append({
            "skuPartNumber": s.get("skuPartNumber") or s.get("skuId"),
            "enabled": enabled,
            "consumed": consumed,
            "remaining": max(0, enabled - consumed),
            "utilisationPct": util,
            "servicePlansCount": len(plans),
            "Details": { "skuPartNumber": s.get("skuPartNumber") or s.get("skuId"), "servicePlans": plans },
        })
    out.sort(key=lambda r: (r["utilisationPct"], r["consumed"]), reverse=True)
    return out

# ----- Extra overview enrichments -----

CRITICAL_ROLE_NAMES = [
    "Global Administrator",
    "Privileged Role Administrator",
    "User Administrator",
    "Security Administrator",
    "Application Administrator",
    "Cloud Application Administrator",
]

def _unique_principals(rows: List[Dict[str,Any]]) -> int:
    return len({r.get("principalId") for r in (rows or []) if r.get("principalId")})

def _critical_role_assignments(client, name_to_id: Dict[str,str]) -> List[Dict[str,Any]]:
    """Return scalar counts by critical role (permanent assignments only for simplicity)."""
    out = []
    try:
        all_assign = _try_get_all(client, "roleManagement/directory/roleAssignments?$select=id,principalId,roleDefinitionId")
    except Exception:
        all_assign = _try_get_all(client, "roleManagement/directory/roleAssignments")
    by_role: Dict[str, List[Dict[str,Any]]] = {}
    for a in (all_assign or []):
        rid = a.get("roleDefinitionId")
        if not rid: continue
        by_role.setdefault(rid, []).append(a)
    for rn in CRITICAL_ROLE_NAMES:
        rid = name_to_id.get(rn)
        if not rid: continue
        out.append({
            "role": rn,
            "permanentPrincipals": _unique_principals(by_role.get(rid, [])),
        })
    return out

def _pim_totals(client) -> Dict[str,int]:
    """Lightweight totals for PIM (active/eligible)."""
    act = _count_entity(client, "roleManagement/directory/roleAssignmentScheduleInstances?$count=true&$top=1",
                        fallback_list_path="roleManagement/directory/roleAssignmentScheduleInstances")
    eli = _count_entity(client, "roleManagement/directory/roleEligibilityScheduleInstances?$count=true&$top=1",
                        fallback_list_path="roleManagement/directory/roleEligibilityScheduleInstances")
    return {"pimActive": act, "pimEligible": eli}

def _applications_count(client) -> int:
    """Optional enrichment; requires Application.Read.All"""
    return _count_entity(client, "applications?$count=true&$top=1", fallback_list_path="applications")

def _apps_expiring_credentials(client) -> Tuple[List[Dict[str,Any]], Dict[str,int]]:
    """
    Return (top rows, counts by window). Each row is scalar: app, type, daysToExpiry, date.
    """
    rows = _try_get_all(client, "applications?$select=displayName,appId,passwordCredentials,keyCredentials&$top=999") or []
    now = datetime.now(timezone.utc)
    buckets = {"30d":0,"60d":0,"90d":0,"expired":0}
    out_rows: List[Dict[str,Any]] = []

    def _handle(when: str, creds: List[Dict[str,Any]], ctype: str):
        nonlocal out_rows, buckets
        for c in creds or []:
            end = c.get(when)
            if not end: continue
            try:
                dt = datetime.fromisoformat(end.replace("Z","+00:00")) if isinstance(end, str) else end
            except Exception:
                continue
            delta = (dt - now).days
            if delta < 0: buckets["expired"] += 1
            elif delta <= 30: buckets["30d"] += 1
            elif delta <= 60: buckets["60d"] += 1
            elif delta <= 90: buckets["90d"] += 1
            out_rows.append({
                "app": ctype,  # will be replaced below with app name, type
                "type": ctype,
                "daysToExpiry": delta,
                "expiresOn": dt.date().isoformat(),
            })

    for a in rows:
        name = a.get("displayName") or a.get("appId")
        pw = a.get("passwordCredentials") or []
        kc = a.get("keyCredentials") or []
        # append with labelled app
        before_len = len(out_rows)
        _handle("endDateTime", pw, "Secret")
        _handle("endDateTime", kc, "Certificate")
        for i in range(before_len, len(out_rows)):
            out_rows[i]["app"] = name

    # sort soonest first and trim top 15 for the table
    out_rows.sort(key=lambda r: (r["daysToExpiry"] if r["daysToExpiry"] is not None else 999999))
    return out_rows[:15], buckets

def _legacy_auth_blocked_bool(policies: List[Dict[str,Any]]) -> bool:
    """
    Heuristic: any CA policy that BLOCKS and targets clientAppTypes or includes legacy protocols via 'other'.
    """
    for p in (policies or []):
        grant = _safe_get(p, "grantControls") or _safe_get(p, "grantControls_v2") or {}
        builtins = set(grant.get("builtInControls") or [])
        if "block" not in {b.lower() for b in builtins}:
            continue
        cat = set((_safe_get(p, "conditions", "clientAppTypes") or []))
        if cat:  # if client app types scoped (browser/mobileDesktop/other)
            if "other" in {c.lower() for c in cat} or len(cat) > 0:
                return True
    return False

# ----------------------- Main -----------------------

def run(client, args):
    run_id = fncNewRunId("tenantoverview")
    ts = _iso_now()
    fncPrintMessage(f"Running Tenant Overview (run={run_id})", "info")

    # Organisation
    org = _get_organization(client)
    org_name = org.get("displayName") or "(unknown)"
    created = org.get("createdDateTime") or "-"

    # Domains
    domains = _get_domains(client)
    verified_domains = [d for d in domains if d.get("isVerified")]
    default_domain = next((d.get("id") for d in domains if d.get("isDefault")), None)
    initial_domain = next((d.get("id") for d in domains if d.get("isInitial")), None)

    # Subscribed SKUs / Licences
    skus = _get_subscribed_skus(client)
    lic_rows = _summarise_licenses(skus)

    # Counts
    total_users   = _count_entity(client, "users?$count=true&$top=1")
    enabled_users = _count_entity(client, "users?$count=true&$filter=accountEnabled eq true&$top=1", fallback_list_path="users")
    guest_users   = _count_entity(client, "users?$count=true&$filter=userType eq 'Guest'&$top=1", fallback_list_path="users")
    total_groups  = _count_entity(client, "groups?$count=true&$top=1")
    total_sps     = _count_entity(client, "servicePrincipals?$count=true&$top=1")
    ca_policies   = _get_ca_policies_count(client)

    # Group breakdown (security / M365 / other) via brief fetch
    grp_rows = _try_get_all(client, "groups?$select=id,securityEnabled,groupTypes&$top=999")
    sec_g, m365_g, oth_g = _split_group_types(grp_rows or [])

    # Roles
    total_role_defs, custom_role_defs, name_to_id = _get_directory_roles(client)
    crit_assign_rows = _critical_role_assignments(client, name_to_id)

    # PIM mini-summary
    pim_summ = _pim_totals(client)

    # MFA registration (optional)
    mfa_stats = _get_auth_registration_stats(client)

    # Applications + expiring credentials (optional)
    apps_total = _applications_count(client)
    exp_rows, exp_buckets = _apps_expiring_credentials(client) if apps_total else ([], {"30d":0,"60d":0,"90d":0,"expired":0})

    # CA posture (legacy auth heuristic)
    ca_full = _get_ca_policies(client)
    legacy_blocked = _legacy_auth_blocked_bool(ca_full)

    # Console previews (compact + scalar columns only)
    if domains:
        fncPrintMessage("Domains (top 10)", "info")
        preview_domains = [{
            "domain": d.get("id"),
            "verified": bool(d.get("isVerified")),
            "default": bool(d.get("isDefault")),
            "initial": bool(d.get("isInitial")),
            "isRoot": bool(d.get("isRoot")),
            "authType": d.get("authenticationType") or "-",
            "rootDomain": d.get("rootDomain") or "-",
            "supportedServices": ", ".join(d.get("supportedServices") or []) or "-",
        } for d in domains[:10]]
        print(fncToTable(
            preview_domains,
            headers=["domain","verified","default","initial","isRoot","authType","rootDomain","supportedServices"],
            max_rows=10
        ))

    if lic_rows:
        fncPrintMessage("Licence Utilisation (top 10 by %)", "info")
        print(fncToTable(lic_rows[:10],
                         headers=["skuPartNumber","consumed","enabled","remaining","utilisationPct","servicePlansCount"],
                         max_rows=10))

    # KPIs (friendly badge labels, keep tone for colour)
    _kpi_tone_words = {
        "primary": "Info",
        "success": "OK",
        "secondary": "Neutral",
        "warning": "Attention",
        "danger": "Critical",
        "info": "Info",
    }

    # find GA principals count for quick KPI
    ga_count = next((r["permanentPrincipals"] for r in crit_assign_rows if r["role"] == "Global Administrator"), 0)

    kpis = [
        {"label":"Users (Total)","value":str(total_users),
         "tone":"primary","badge":"Total","tone_label":_kpi_tone_words["primary"],"icon":"bi-people"},

        {"label":"Users (Enabled)","value":str(enabled_users),
         "tone":"success","badge":"Enabled","tone_label":_kpi_tone_words["success"],"icon":"bi-person-check"},

        {"label":"Guests","value":str(guest_users),
         "tone":"secondary","badge":"Guests","tone_label":_kpi_tone_words["secondary"],"icon":"bi-person-plus"},

        {"label":"Groups","value":str(total_groups),
         "tone":"primary","badge":"Groups","tone_label":_kpi_tone_words["primary"],"icon":"bi-diagram-3"},

        {"label":"Apps (Service Principals)","value":str(total_sps),
         "tone":"info","badge":"Apps","tone_label":"Info","icon":"bi-box"},

        {"label":"Verified Domains","value":str(len(verified_domains)),
         "tone":"secondary","badge":"Domains","tone_label":_kpi_tone_words["secondary"],"icon":"bi-globe2"},

        {"label":"CA Policies","value":str(ca_policies),
         "tone":"warning","badge":"CA","tone_label":_kpi_tone_words["warning"],"icon":"bi-shield-lock"},

        {"label":"Global Admin Principals","value":str(ga_count),
         "tone": "warning" if ga_count > 2 else "success",
         "badge":"Admins","tone_label": "Attention" if ga_count > 2 else "OK","icon":"bi-person-gear"},

        {"label":"Applications","value":str(apps_total),
         "tone":"primary","badge":"Apps","tone_label":"Info","icon":"bi-app"},

        {"label":"App Secrets ≤30d","value":str(exp_buckets.get("30d",0)),
         "tone":"danger" if exp_buckets.get("30d",0) else "success",
         "badge":"≤30d","tone_label":"Critical" if exp_buckets.get("30d",0) else "OK","icon":"bi-exclamation-octagon"},

        {"label":"Legacy Auth Blocked","value":"Yes" if legacy_blocked else "No",
         "tone":"success" if legacy_blocked else "danger",
         "badge":"Legacy","tone_label":"OK" if legacy_blocked else "Critical","icon":"bi-shield-x"},
    ]

    # Standouts
    standouts = {}
    if lic_rows:
        top_lic = lic_rows[0]
        standouts["group"] = {
            "title":"Most Utilised Licence",
            "name": f"{top_lic['skuPartNumber']}",
            "risk_score": float(min(10.0, (top_lic['utilisationPct']/10.0))),
            "comment": f"{top_lic['consumed']} / {top_lic['enabled']} used ({top_lic['utilisationPct']}%)",
        }
    if default_domain:
        standouts["user"] = {
            "title":"Default Domain",
            "name": default_domain,
            "risk_score": 3.0 if default_domain.endswith(".onmicrosoft.com") else 1.0,
            "comment": "Default domain is onmicrosoft.com" if default_domain.endswith(".onmicrosoft.com") else "Custom domain is default",
        }
    standouts["computer"] = {
        "title":"Conditional Access",
        "name": f"{ca_policies} policies",
        "risk_score": 2.0 if ca_policies else 8.0,
        "comment": "No CA policies found" if not ca_policies else ("Legacy auth blocked" if legacy_blocked else "Legacy auth not blocked"),
    }

    # Charts
    user_chart_labels = ["Enabled","Guests","Others"]
    others = max(0, total_users - enabled_users - guest_users)
    user_chart_values = [enabled_users, guest_users, others]

    lic_top = lic_rows[:6]
    lic_labels = [r["skuPartNumber"] for r in lic_top] or ["(none)"]
    lic_values = [r["utilisationPct"] for r in lic_top] or [0]

    # Tables (scalar-only; extras for drawers under "Details")
    org_profile = [{
        "Tenant Name": org_name,
        "Created": created,
        "Default Domain": default_domain or "-",
        "Initial Domain": initial_domain or "-",
        "Tech Contacts": ", ".join(org.get("technicalNotificationMails") or []) or "-",
        "Security Contacts": ", ".join(org.get("securityComplianceNotificationMails") or []) or "-",
        "Marketing Contacts": ", ".join(org.get("marketingNotificationEmails") or []) or "-",
        "Tenant Type": org.get("tenantType") or "-",
        "Privacy": _safe_get(org, "privacyProfile", "contactEmail", default="-"),
    }]

    domain_rows = [{
        "domain": d.get("id"),
        "verified": bool(d.get("isVerified")),
        "default": bool(d.get("isDefault")),
        "initial": bool(d.get("isInitial")),
        "isRoot": bool(d.get("isRoot")),
        "authType": d.get("authenticationType") or "-",
        "rootDomain": d.get("rootDomain") or "-",
        "supportedServices": ", ".join(d.get("supportedServices") or []) or "-",
    } for d in (domains or [])]

    role_summary_rows = [{
        "totalRoleDefinitions": total_role_defs,
        "customRoleDefinitions": custom_role_defs,
        "builtInRoleDefinitions": max(0, total_role_defs - custom_role_defs),
        "notes": "Custom roles > 0 indicates bespoke role definitions",
    }]

    critical_role_rows = crit_assign_rows or []

    group_breakdown_rows = [{
        "securityGroups": sec_g,
        "m365Groups": m365_g,
        "otherGroups": oth_g,
        "sampleSizeTop": len(grp_rows or []),
    }]

    app_expiring_rows = exp_rows or []

    ca_posture_rows = [{
        "policiesTotal": ca_policies,
        "legacyAuthBlocked": bool(legacy_blocked),
    }]

    pim_rows = [{
        "pimActive": pim_summ.get("pimActive", 0),
        "pimEligible": pim_summ.get("pimEligible", 0),
    }]

    # Dashboard summary numbers
    summary = {
        "Users (Total)": total_users,
        "Users (Enabled)": enabled_users,
        "Guest Users": guest_users,
        "Groups": total_groups,
        "Apps (Service Principals)": total_sps,
        "Applications": apps_total,
        "Verified Domains": len(verified_domains),
        "CA Policies": ca_policies,
        "Legacy Auth Blocked": bool(legacy_blocked),
        "Custom Directory Roles": custom_role_defs,
        "Global Admin Principals": ga_count,
        "PIM Active": pim_summ.get("pimActive", 0),
        "PIM Eligible": pim_summ.get("pimEligible", 0),
        "App Secrets ≤30d": exp_buckets.get("30d", 0),
        "App Secrets ≤60d": exp_buckets.get("60d", 0),
        "App Secrets ≤90d": exp_buckets.get("90d", 0),
        "App Secrets Expired": exp_buckets.get("expired", 0),
        "MFA Registered Users": mfa_stats.get("registeredUsers", 0),
        "MFA Capable Users": mfa_stats.get("mfaCapableUsers", 0),
    }

    # ----------------------- Recommendations -----------------------
    recos: List[Dict[str,Any]] = []

    if ca_policies == 0:
        recos.append({
            "label":"Enable Conditional Access",
            "severity":"danger",
            "text":"No Conditional Access (CA) policies detected. This leaves sign-in risk unmanaged.",
            "actionsText":"Define baseline policies • Pilot in report-only • Enforce after validation",
            "refsText":"-",
        })
    elif not legacy_blocked:
        recos.append({
            "label":"Block Legacy Authentication",
            "severity":"warning",
            "text":"CA policies found, but legacy protocols do not appear to be blocked.",
            "actionsText":"Add client app condition (Other) • Grant: Block • Scope to all users with break-glass excluded",
            "refsText":"-",
        })

    if default_domain and default_domain.endswith(".onmicrosoft.com"):
        recos.append({
            "label":"Set a Custom Default Domain",
            "severity":"warning",
            "text":"The default sign-in domain is the initial onmicrosoft.com. This can be confusing and less professional for users.",
            "actionsText":"Verify a custom domain • Set as default for new UPNs",
            "refsText":"-",
        })

    if len(verified_domains) == 0:
        recos.append({
            "label":"Verify Production Domains",
            "severity":"danger",
            "text":"No verified domains were found. Mail routing and user sign-in may rely on onmicrosoft.com only.",
            "actionsText":"Add and verify at least one business domain • Configure SPF/DKIM/DMARC if mail is in scope",
            "refsText":"-",
        })

    if total_users > 0:
        reg = mfa_stats.get("registeredUsers", 0)
        capable = mfa_stats.get("mfaCapableUsers", 0)
        numerator = reg or capable
        pct = round((numerator / total_users) * 100.0, 1) if total_users else 0.0
        if numerator == 0:
            recos.append({
                "label":"Mandate MFA Registration",
                "severity":"danger",
                "text":"No users appear to be registered or capable for MFA.",
                "actionsText":"Roll out phishing-resistant methods • Enforce via CA with only break-glass excluded",
                "refsText":"-",
            })
        elif pct < 80.0:
            recos.append({
                "label":"Improve MFA Coverage",
                "severity":"warning",
                "text":f"MFA coverage is approximately {pct}% of users.",
                "actionsText":"Target remaining users • Prefer passkeys/FIDO2 or Authenticator over SMS/Voice",
                "refsText":"-",
            })

    high_util = [l for l in lic_rows if l["utilisationPct"] >= 90.0]
    if high_util:
        recos.append({
            "label":"Address Licence Saturation",
            "severity":"warning",
            "text":"One or more licences are ≥90% utilised.",
            "actionsText":"Remove inactive/duplicate assignments • Consider additional capacity or alternative plans",
            "refsText":"-",
        })

    if ga_count > 2:
        recos.append({
            "label":"Reduce Global Administrator Footprint",
            "severity":"warning",
            "text":f"{ga_count} principals have Global Administrator permanently assigned.",
            "actionsText":"Move to eligible via PIM • Use least-privilege roles • Prefer break-glass + PIM",
            "refsText":"-",
        })

    if exp_buckets.get("30d",0) or exp_buckets.get("expired",0):
        recos.append({
            "label":"Rotate Expiring App Credentials",
            "severity":"danger" if exp_buckets.get("expired",0) else "warning",
            "text":f"{exp_buckets.get('expired',0)} expired, {exp_buckets.get('30d',0)} expiring within 30 days.",
            "actionsText":"Rotate secrets/certs • Consider certificate-based creds • Implement expiry monitoring",
            "refsText":"-",
        })

    if total_users > 0 and guest_users / max(1, total_users) > 0.25:
        pct = round((guest_users / total_users) * 100.0, 1)
        recos.append({
            "label":"Review Guest Access",
            "severity":"warning",
            "text":f"Guests comprise ~{pct}% of directory users.",
            "actionsText":"Apply guest-specific CA • Enable access reviews on guest-heavy groups/teams",
            "refsText":"-",
        })

    if custom_role_defs > 0:
        recos.append({
            "label":"Review Custom Directory Roles",
            "severity":"info",
            "text":"Custom role definitions exist; ensure least-privilege and documentation.",
            "actionsText":"Audit permissions and assignments • Remove unused roles",
            "refsText":"-",
        })

    if recos:
        fncPrintMessage("Recommendations (top 5)", "info")
        preview = [{"severity": r["severity"], "label": r["label"], "note": r["text"]} for r in recos[:5]]
        print(fncToTable(preview, headers=["severity","label","note"], max_rows=5))

    data = {
        "provider": "entra",
        "run_id": run_id,
        "timestamp": ts,
        "summary": summary,

        # Tables (scalar-only)
        "organisation_profile": org_profile,
        "domains": domain_rows,
        "licences": lic_rows,
        "role_summary": role_summary_rows,
        "critical_role_summary": critical_role_rows,
        "group_breakdown": group_breakdown_rows,
        "pim_summary": pim_rows,
        "ca_posture": ca_posture_rows,
        "apps_expiring_credentials": app_expiring_rows,

        # Dashboard
        "_kpis": kpis,
        "_standouts": standouts,
        "_charts": {
            "place": "summary",
            "usersByType": {"labels": user_chart_labels, "data": user_chart_values},
            "licenceUtilisation": {"labels": lic_labels, "data": lic_values},
        },

        # Recommendations (scalar fields only, with compact actionsText)
        "_recommendations": recos,

        "_title": "Tenant Overview",
        "_subtitle": "High-level Entra tenant posture, objects, domains, licence utilisation, and recommendations",
        "_container_class": "toverview",
        "_inline_css": OVERVIEW_CSS,
        "_inline_js": OVERVIEW_JS,
    }

    # Optional HTML report output
    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "tenant_overview", data)

    fncPrintMessage("Tenant Overview module complete.", "success")
    return data
