# ================================================================
# File     : modules/entra/sp_risk_audit.py
# Purpose  : Entra Service Principal & App Registration Risk Audit
#            FAST: bulk oauth2PermissionGrants (delegated)
#            DEEP: add per-SP appRoleAssignments (application perms)
#            PLUS: App Registrations' declared permissions (RRA)
# ================================================================

### WIP

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Iterable
import math

from core.utils import (
    fncPrintMessage,
    fncToTable,
    fncNewRunId,
)
from core.reporting import fncWriteHTMLReport

REQUIRED_PERMS = [
    "Directory.Read.All",
    "Application.Read.All",
]

# ----------------------- Module-local CSS/JS ---------------------

SP_CSS = r"""
.sp-audit .card table { table-layout:auto }
.sp-audit table thead th{ position:sticky; top:0; z-index:2 }

.sp-audit .spa-toolbar{
  display:flex; gap:10px; flex-wrap:wrap; align-items:center;
  margin:6px 2px 0 2px;
}
.sp-audit .spa-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:260px; outline:none;
}
.sp-audit .spa-toolbar .btn{
  padding:6px 12px; border:1px solid var(--border); border-radius:999px;
  background:var(--card); cursor:pointer; font-weight:600;
}
.sp-audit .spa-toolbar .btn.primary{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}

/* Drawer / details like CA audit */
.sp-audit .spa-clickable{ cursor:pointer }
.sp-audit .spa-clickable:hover{ background:rgba(255,255,255,.05) }
.sp-audit .spa-expander > td{ padding:0; background:color-mix(in srgb, var(--card) 92%, #000 8%) }
.sp-audit .spa-expander-body{ padding:14px 16px; border-top:1px solid var(--border) }

.sp-audit .spa-flex{ display:grid; grid-template-columns: 1fr 1.25fr; gap:16px }
@media (max-width: 1100px){ .sp-audit .spa-flex{ grid-template-columns: 1fr } }
.sp-audit .spa-pane{
  border:1px solid var(--border); border-radius:10px;
  background:color-mix(in srgb, var(--card) 94%, #000 6%); overflow:hidden;
}
.sp-audit .spa-pane h5{
  margin:0; padding:10px 12px;
  background:color-mix(in srgb, var(--accent2) 85%, #000 15%);
  color:#fff; font-weight:700; border-bottom:1px solid rgba(0,0,0,.2);
}
.sp-audit .spa-kv{ width:100%; border-collapse:collapse }
.sp-audit .spa-kv th,.sp-audit .spa-kv td{
  text-align:left; padding:8px 12px; border-bottom:1px solid var(--border); vertical-align:top;
}
.sp-audit .spa-kv th{
  width:220px; white-space:nowrap;
  background:color-mix(in srgb, var(--accent2) 12%, var(--card)); font-weight:600;
}
.sp-audit .spa-kv tr:last-child td,.sp-audit .spa-kv tr:last-child th{ border-bottom:0 }

/* Wrap JSON/long text */
.sp-audit .cp-json, .sp-audit .cp-json pre, .sp-audit .cp-json code{
  white-space:pre-wrap !important; word-break:break-word !important; overflow-x:hidden !important;
}

/* Hide some columns in grid but keep in the drawer */
.sp-audit .spa-hide { display:none !important; }
"""

SP_JS = r"""
(function(){
  const root = document.querySelector('.sp-audit') || document;

  function addToolbar(table, title){
    const card = table.closest('.card');
    if (!card) return null;
    const bar = document.createElement('div');
    bar.className = 'spa-toolbar';
    bar.innerHTML = `
      <input type="search" placeholder="Search ${title}…" aria-label="Search ${title}">
      <button class="btn primary" data-action="viewmore" style="display:none">Show more…</button>
    `;
    card.insertBefore(bar, card.querySelector('.tablewrap'));
    return bar;
  }

  function paginateAndSearch(table, toolbar, pageSize=20){
    const search = toolbar.querySelector('input[type="search"]');
    const btn    = toolbar.querySelector('[data-action="viewmore"]');
    const rows   = Array.from(table.querySelectorAll('tbody tr'));
    let expanded = false;

    function apply(){
      const q = (search.value || '').toLowerCase();
      let shown = 0;
      rows.forEach(tr=>{
        const ok = !q || tr.textContent.toLowerCase().includes(q);
        if (!ok){ tr.style.display = 'none'; return; }
        if (!expanded && !q && shown >= pageSize){ tr.style.display = 'none'; return; }
        tr.style.display = ''; shown++;
      });
      const more = !expanded && !q && shown < rows.length;
      btn.style.display = more ? '' : 'none';
    }

    search.addEventListener('input', apply);
    btn.addEventListener('click', ()=>{ expanded = true; apply(); });
    apply();
  }

  function hideColumns(table, hideNames){
    const ths = Array.from(table.querySelectorAll('thead th'));
    const idx = ths.map((th,i)=>({name:(th.textContent||'').trim(), i}));
    for (const {name,i} of idx){
      if (hideNames.has(name)){
        table.querySelectorAll(`thead th:nth-child(${i+1}), tbody td:nth-child(${i+1})`)
             .forEach(c => c.classList.add('spa-hide'));
      }
    }
    return idx.reduce((m,{name,i}) => (m[name]=i, m), {});
  }

  function attachRowDrawer(table, build){
    if(!table) return;
    const heads = Array.from(table.querySelectorAll('thead th')).map(h=> (h.textContent||'').trim());
    const totalCols = heads.length;
    Array.from(table.querySelectorAll('tbody tr')).forEach(tr=>{
      tr.classList.add('spa-clickable');
      tr.addEventListener('click', ()=>{
        const open = tr.classList.contains('spa-open');
        const next = tr.nextElementSibling;
        if (next && next.classList.contains('spa-expander')) next.remove();
        tr.classList.remove('spa-open');
        if (open) return;

        const row = {};
        heads.forEach((name, idx)=>{
          const td = tr.children[idx];
          row[name] = td ? td.innerHTML : '';
        });

        const html = build(row);
        const exp = document.createElement('tr');
        const td  = document.createElement('td');
        exp.className = 'spa-expander';
        td.colSpan = totalCols;
        td.innerHTML = html;
        exp.appendChild(td);
        tr.parentNode.insertBefore(exp, tr.nextSibling);
        tr.classList.add('spa-open');
      });
    });
  }

  const buildDrawer = (row)=>`
    <div class="spa-expander-body">
      <div class="spa-flex">
        <div class="spa-pane">
          <h5>Service Principal</h5>
          <table class="spa-kv"><tbody>
            <tr><th>Name</th><td>${row['Name']||'-'}</td></tr>
            <tr><th>App ID</th><td>${row['AppId']||'-'}</td></tr>
            <tr><th>Object ID</th><td>${row['ObjectId']||'-'}</td></tr>
            <tr><th>Enabled</th><td>${row['Enabled']||'-'}</td></tr>
            <tr><th>Multi-tenant</th><td>${row['MultiTenant']||'-'}</td></tr>
            <tr><th>Verified Publisher</th><td>${row['VerifiedPublisher']||'-'}</td></tr>
            <tr><th>Owners</th><td class="cp-json">${row['Owners']||'-'}</td></tr>
          </tbody></table>
        </div>
        <div class="spa-pane">
          <h5>Risk & Permissions</h5>
          <table class="spa-kv"><tbody>
            <tr><th>Risk Bucket</th><td>${row['Bucket']||'-'} (score ${row['RiskScore']||'-'})</td></tr>
            <tr><th>Potential Impact</th><td class="cp-json">${row['PotentialImpact']||'-'}</td></tr>
            <tr><th>App Permissions</th><td class="cp-json">${row['AppPermissions']||'<em>none</em>'}</td></tr>
            <tr><th>Delegated Grants</th><td class="cp-json">${row['DelegatedGrants']||'<em>none</em>'}</td></tr>
            <tr><th>Credential State</th><td class="cp-json">${row['CredentialState']||'-'}</td></tr>
            <tr><th>Findings</th><td class="cp-json">${row['Findings']||'-'}</td></tr>
          </tbody></table>
        </div>
      </div>
    </div>`;

  function enhance(tableId, title){
    const tbl = root.querySelector('#'+tableId);
    if(!tbl) return;
    // hide technical columns in grid
    hideColumns(tbl, new Set(['AppId','ObjectId','VerifiedPublisher','Owners','AppPermissions','DelegatedGrants','PotentialImpact','CredentialState','Findings']));
    const bar = addToolbar(tbl, title);
    if (bar) paginateAndSearch(tbl, bar, 20);
    attachRowDrawer(tbl, buildDrawer);
  }

  enhance('tbl-sp-findings','Service Principals');
})();
"""

# ----------------------- Helpers -----------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _sanitize_table(rows: Iterable[Dict[str, Any]], headers: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        if isinstance(r, dict):
            out.append({h: r.get(h, "") for h in headers})
        else:
            d = {h: "" for h in headers}
            d[headers[0]] = str(r)
            out.append(d)
    return out

def _days_left(dt_str: str | None) -> int | None:
    if not dt_str:
        return None
    try:
        s = str(dt_str).rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        return int(delta.days)
    except Exception:
        return None

def _bucket_from_score(s: int) -> str:
    if s >= 80: return "critical"
    if s >= 50: return "warning"
    return "ok"

# Workload Identities Premium (heuristic match on subscribedSkus)
WIP_SKU_HINTS = {
    "WORKLOAD", "WORKLOAD_ID", "WORKLOADIDENTITY", "ENTRA_WI", "ENTRA_WIP", "AADWI", "WIP"
}

def _has_wip_license(client) -> Dict[str, Any]:
    info = {"present": False, "matchedSku": None, "totalUnits": 0, "consumed": 0}
    try:
        skus = client.get_all("subscribedSkus")
        for s in skus or []:
            sku = (s.get("skuPartNumber") or "").upper()
            if any(h in sku for h in WIP_SKU_HINTS):
                info["present"] = True
                info["matchedSku"] = s.get("skuPartNumber")
                info["totalUnits"] = int((s.get("prepaidUnits") or {}).get("enabled", 0) or 0)
                info["consumed"]   = int(s.get("consumedUnits", 0) or 0)
                break
    except Exception as ex:
        fncPrintMessage(f"[SP] Could not inspect subscribed SKUs for WIP: {ex}", "warn")
    return info

# Risk scoring knobs
HIGH_IMPACT_PERMS = {
    # Microsoft Graph (common app roles)
    "Directory.Read.All", "Directory.ReadWrite.All", "Directory.AccessAsUser.All",
    "User.ReadWrite.All", "Group.ReadWrite.All",
    "Application.ReadWrite.All", "AppRoleAssignment.ReadWrite.All",
    "Policy.Read.All", "Policy.ReadWrite.ConditionalAccess",
    "RoleManagement.Read.Directory", "RoleManagement.ReadWrite.Directory",
    # Azure RM / others can appear as resourceAppId != Graph
}

def _score_sp(sp: Dict[str, Any], perms_text: str, cred_state: Dict[str, Any]) -> Tuple[int, List[str]]:
    score = 0
    flags: List[str] = []

    if sp.get("accountEnabled", True): score += 10
    if not sp.get("owners"): score += 20; flags.append("No owners")
    if (sp.get("verifiedPublisher") or {}).get("verifiedPublisherId") in (None, ""):
        score += 10; flags.append("No verified publisher")
    if sp.get("servicePrincipalType") == "ManagedIdentity":
        # managed identities: typically scoped; still consider permissions
        score += 5

    # Multi-tenant increases exposure
    if sp.get("signInAudience") in ("AzureADMultipleOrgs", "AzureADandPersonalMicrosoftAccount"):
        score += 10; flags.append("Multi-tenant")

    # App permissions
    hits = [p for p in HIGH_IMPACT_PERMS if p in perms_text]
    if hits:
        score += 40; flags.append("High-impact app perms: " + ", ".join(hits[:5]) + ("…" if len(hits) > 5 else ""))

    # Credential state
    expired = cred_state.get("expired", 0)
    near    = cred_state.get("near", 0)
    many    = cred_state.get("count", 0)
    if expired: score += 25; flags.append(f"{expired} expired credential(s)")
    if near:    score += 15; flags.append(f"{near} near expiry")
    if many >= 4: score += 10; flags.append(f"{many} total credentials")

    score = max(0, min(100, score))
    return score, flags

def _summarize_creds(sp: Dict[str, Any]) -> Dict[str, Any]:
    pw = sp.get("passwordCredentials") or []
    kc = sp.get("keyCredentials") or []
    expired = near = 0
    for c in pw + kc:
        d = _days_left(c.get("endDateTime"))
        if d is None: continue
        if d < 0: expired += 1
        elif d <= 30: near += 1
    return {
        "count": len(pw) + len(kc),
        "expired": expired,
        "near": near,
        "text": f"{len(pw)} secrets, {len(kc)} certs — {expired} expired, {near} <=30d"
    }

def _list_to_csv(vals: Iterable[str]) -> str:
    return ", ".join(sorted(set([v for v in vals if v])))

def _fetch_app_role_assignments(client, sp_id: str) -> List[Dict[str, Any]]:
    try:
        return client.get_all(f"servicePrincipals/{sp_id}/appRoleAssignments?$select=id,resourceId,principalId,appRoleId")
    except Exception:
        return client.get_all(f"servicePrincipals/{sp_id}/appRoleAssignments")

def _fetch_oauth2_grants(client, sp_id: str) -> List[Dict[str, Any]]:
    # Delegated grants to this SP (client) for a resource scope
    try:
        return client.get_all(f"servicePrincipals/{sp_id}/oauth2PermissionGrants?$select=id,resourceId,scope,consentType,principalId")
    except Exception:
        return client.get_all(f"servicePrincipals/{sp_id}/oauth2PermissionGrants")

def _get_owners(client, sp_id: str) -> List[Dict[str, Any]]:
    try:
        return client.get_all(f"servicePrincipals/{sp_id}/owners?$select=id,displayName,userPrincipalName")
    except Exception:
        return client.get_all(f"servicePrincipals/{sp_id}/owners")

# ----------------------- Main -----------------------

def run(client, args):
    run_id = fncNewRunId("sp-audit")
    ts = _iso_now()
    fncPrintMessage(f"Running Service Principal Risk Audit (run={run_id})", "info")

    # Licensing: Workload Identities Premium
    wip = _has_wip_license(client)

    # Fetch SPs (lean $select, tolerate fallback)
    try:
        sps = client.get_all(
            "servicePrincipals?"
            "$select=id,displayName,appId,accountEnabled,servicePrincipalType,signInAudience,appOwnerOrganizationId,"
            "verifiedPublisher,passwordCredentials,keyCredentials"
        )
    except Exception:
        sps = client.get_all("servicePrincipals")

    findings: List[Dict[str, Any]] = []
    total_high_perm = 0
    total_no_owner  = 0
    total_expired   = 0
    total_multi     = 0

    # Pre-cache owners & permissions per SP lazily
    for sp in sps or []:
        sp_id = sp.get("id")
        name  = sp.get("displayName") or sp.get("appId") or sp_id

        # Owners
        owners = _get_owners(client, sp_id)
        owners_txt = _list_to_csv([o.get("displayName") or o.get("userPrincipalName") or o.get("id") for o in owners])

        # App permissions (application role assignments)
        app_roles   = _fetch_app_role_assignments(client, sp_id)
        # Delegated grants issued to this app (rare for app creds scenarios, but informative)
        del_grants  = _fetch_oauth2_grants(client, sp_id)

        # Convert permissions to readable text
        app_perm_txt = []
        for a in app_roles or []:
            # We don't expand resourceId to name here (extra round-trips); show ids compactly
            app_perm_txt.append(f"resource:{a.get('resourceId','?')} appRoleId:{a.get('appRoleId','?')}")
        app_perm_str = "; ".join(app_perm_txt) if app_perm_txt else ""

        scope_txt = []
        for g in del_grants or []:
            sc = g.get("scope")
            if sc:
                scope_txt.append(sc)
        del_grants_str = _list_to_csv(scope_txt)

        # Credentials
        cred_state = _summarize_creds(sp)

        # Risk score
        perms_for_scoring = f"{app_perm_str} {del_grants_str}"
        score, flags = _score_sp(sp, perms_for_scoring, cred_state)
        bucket = _bucket_from_score(score)

        # Potential impact narrative
        impact_bits = []
        if "High-impact app perms" in " ".join(flags) or "Directory" in perms_for_scoring:
            impact_bits.append(
                "With these application permissions, valid app credentials could read or modify directory objects via the target resource APIs."
            )
        if cred_state["expired"] > 0:
            impact_bits.append("Has expired credentials — attackers may still hold leaked secrets/certs.")
        if not owners:
            impact_bits.append("No owners: harder to detect or rotate creds; create break-glass ownership.")
        if sp.get("accountEnabled", True) and perms_for_scoring:
            impact_bits.append("If anyone obtains this app's credentials, they can use its permissions without user consent.")

        potential_impact = " ".join(impact_bits) or "If credentials are obtained, actions are limited to the app’s assigned permissions."

        # Tally for KPIs
        if not owners: total_no_owner += 1
        if cred_state["expired"]: total_expired += 1
        if bucket != "ok": total_high_perm += 1 if "High-impact app perms" in " ".join(flags) else total_high_perm
        if sp.get("signInAudience") in ("AzureADMultipleOrgs", "AzureADandPersonalMicrosoftAccount"): total_multi += 1

        findings.append({
            "Name": name,
            "Enabled": "Yes" if sp.get("accountEnabled", True) else "No",
            "MultiTenant": "Yes" if sp.get("signInAudience") in ("AzureADMultipleOrgs","AzureADandPersonalMicrosoftAccount") else "No",
            "Bucket": bucket,
            "RiskScore": str(score),
            # hidden in grid, visible in drawer
            "AppId": sp.get("appId",""),
            "ObjectId": sp_id,
            "VerifiedPublisher": "Yes" if (sp.get("verifiedPublisher") or {}).get("verifiedPublisherId") else "No",
            "Owners": owners_txt or "-",
            "AppPermissions": app_perm_str or "",
            "DelegatedGrants": del_grants_str or "",
            "CredentialState": cred_state["text"],
            "PotentialImpact": potential_impact,
            "Findings": "; ".join(flags) if flags else "-",
        })

    # ---------- Console preview ----------
    headers = ["Name","Enabled","MultiTenant","Bucket","RiskScore"]
    safe_rows = _sanitize_table(findings, headers + [
        "AppId","ObjectId","VerifiedPublisher","Owners","AppPermissions","DelegatedGrants","CredentialState","PotentialImpact","Findings"
    ])
    fncPrintMessage("Service Principals — top 25 by risk", "info")
    # Sort by score desc for preview
    safe_rows.sort(key=lambda r: int(r.get("RiskScore") or 0), reverse=True)
    print(fncToTable(safe_rows[:25], headers=headers, max_rows=min(25, len(safe_rows))))

    # ---------- KPIs / charts ----------
    total = len(findings)
    critical = sum(1 for r in findings if r["Bucket"]=="critical")
    warning  = sum(1 for r in findings if r["Bucket"]=="warning")
    ok_count = total - critical - warning

    kpis = [
        {"label":"Service Principals","value":str(total),"tone":"primary","icon":"bi-diagram-3"},
        {"label":"Critical","value":str(critical),"tone":"danger","icon":"bi-exclamation-octagon"},
        {"label":"Warning","value":str(warning),"tone":"warning","icon":"bi-exclamation-triangle"},
        {"label":"No Owners","value":str(total_no_owner),"tone":"secondary","icon":"bi-person-x"},
        {"label":"Expired Creds","value":str(total_expired),"tone":"secondary","icon":"bi-clock-history"},
        {"label":"Multi-tenant","value":str(total_multi),"tone":"secondary","icon":"bi-people"},
        {"label":"WIP License","value":("Present" if wip["present"] else "Not detected"),"tone":"info","icon":"bi-patch-check" if wip["present"] else "bi-patch-exclamation"},
    ]

    charts = {
        "place": "summary",
        "severity": {"labels": ["Critical","Warning","OK"], "data":[critical, warning, ok_count]},
    }

    # Standouts
    standouts = {}
    if safe_rows:
        top = safe_rows[0]
        standouts["group"] = {
            "title":"Highest-Risk Service Principal",
            "name": f"{top['Name']}",
            "risk_score": float(min(10.0, (int(top['RiskScore'])/10.0))),
            "comment": f"{top['Findings'][:140]}{'…' if len(top['Findings'])>140 else ''}"
        }

    # Summary KV
    sp_summary = [
        {"Field":"Total Service Principals","Value": total},
        {"Field":"Critical","Value": critical},
        {"Field":"Warning","Value": warning},
        {"Field":"OK","Value": ok_count},
        {"Field":"No Owners","Value": total_no_owner},
        {"Field":"Expired Credentials","Value": total_expired},
        {"Field":"Multi-tenant","Value": total_multi},
        {"Field":"Workload IDs Premium","Value": ("Yes" if wip["present"] else "No")},
        {"Field":"WIP Matched SKU","Value": (wip["matchedSku"] or "-")},
    ]

    data = {
        "provider": "entra",
        "run_id": run_id,
        "timestamp": ts,

        "sp_summary": sp_summary,
        "sp_findings": safe_rows,     # full rows; reporter shows drawers

        "wip_licensing": wip,         # present/sku/units/consumed for other modules

        "_kpis": kpis,
        "_standouts": standouts,
        "_charts": charts,

        "_title": "Service Principal Risk Audit",
        "_subtitle": "Risky app permissions, creds posture, & potential impact if credentials are stolen",
        "_container_class": "sp-audit",
        "_inline_css": SP_CSS,
        "_inline_js":  SP_JS,
    }

    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "sp_risk_audit", data)

    fncPrintMessage("Service Principal Risk Audit module complete.", "success")
    return data
