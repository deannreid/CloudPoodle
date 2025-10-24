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

REQUIRED_PERMS = ["Directory.Read.All", "Application.Read.All"]

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
  width:230px; white-space:nowrap;
  background:color-mix(in srgb, var(--accent2) 12%, var(--card)); font-weight:600;
}
.sp-audit .spa-kv tr:last-child td,.sp-audit .spa-kv tr:last-child th{ border-bottom:0 }
.sp-audit .cp-json, .sp-audit .cp-json pre, .sp-audit .cp-json code{
  white-space:pre-wrap !important; word-break:break-word !important; overflow-x:hidden !important;
}
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
          <h5>${row['Type']==='AppReg' ? 'App Registration' : 'Service Principal'}</h5>
          <table class="spa-kv"><tbody>
            <tr><th>Name</th><td>${row['Name']||'-'}</td></tr>
            <tr><th>Type</th><td>${row['Type']||'-'}</td></tr>
            <tr><th>App ID</th><td>${row['AppId']||'-'}</td></tr>
            <tr><th>Object ID</th><td>${row['ObjectId']||'-'}</td></tr>
            <tr><th>Enabled</th><td>${row['Enabled']||'-'}</td></tr>
            <tr><th>Owners</th><td class="cp-json">${row['Owners']||'-'}</td></tr>
            <tr><th>Linked Service Principal</th><td>${row['LinkedSP']||'-'}</td></tr>
          </tbody></table>
        </div>
        <div class="spa-pane">
          <h5>Risk & Permissions</h5>
          <table class="spa-kv"><tbody>
            <tr><th>Risk Bucket</th><td>${row['Bucket']||'-'} (score ${row['RiskScore']||'-'})</td></tr>
            <tr><th>Reason / Findings</th><td class="cp-json">${row['Findings']||'-'}</td></tr>
            <tr><th>Delegated Grants (consented)</th><td class="cp-json">${row['DelegatedGrants']||'<em>none</em>'}</td></tr>
            <tr><th>Application Permissions (consented)</th><td class="cp-json">${row['AppPermissions']||'<em>none</em>'}</td></tr>
            <tr><th>Declared Permissions (App Registration)</th><td class="cp-json">${row['DeclaredPerms']||'<em>none</em>'}</td></tr>
            <tr><th>Credential State</th><td class="cp-json">${row['CredentialState']||'-'}</td></tr>
            <tr><th>Mode</th><td>${row['Mode']||'-'}</td></tr>
          </tbody></table>
        </div>
      </div>
    </div>`;

  function enhance(tableId, title){
    const tbl = root.querySelector('#'+tableId);
    if(!tbl) return;
    hideColumns(tbl, new Set(['AppId','ObjectId','Owners','DelegatedGrants','AppPermissions','DeclaredPerms','CredentialState','Findings','LinkedSP','Mode']));
    const bar = addToolbar(tbl, title);
    if (bar) paginateAndSearch(tbl, bar, 20);
    attachRowDrawer(tbl, buildDrawer);
  }

  enhance('tbl-sp-findings','Apps & Service Principals');
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

WIP_SKU_HINTS = {"WORKLOAD", "WORKLOAD_ID", "WORKLOADIDENTITY", "ENTRA_WI", "ENTRA_WIP", "AADWI", "WIP"}

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

# Risk knobs
HIGH_IMPACT_PERMS = {
    # common high-priv scopes/roles (delegated/app)
    "Directory.Read.All", "Directory.ReadWrite.All", "Directory.AccessAsUser.All",
    "User.ReadWrite.All", "Group.ReadWrite.All",
    "Application.ReadWrite.All", "AppRoleAssignment.ReadWrite.All",
    "Policy.Read.All", "Policy.ReadWrite.ConditionalAccess",
    "RoleManagement.Read.Directory", "RoleManagement.ReadWrite.Directory",
}

def _score_obj(enabled: bool, owners_cnt: int, multi: bool, perm_hits: int, cred_state: Dict[str, Any]) -> Tuple[int, List[str]]:
    score = 0
    flags: List[str] = []
    if enabled: score += 10
    if owners_cnt == 0: score += 20; flags.append("No owners")
    if multi: score += 10; flags.append("Multi-tenant or broad audience")
    if perm_hits: score += 30; flags.append(f"High-impact permissions ({perm_hits})")
    expired = cred_state.get("expired", 0); near = cred_state.get("near", 0); many = cred_state.get("count", 0)
    if expired: score += 25; flags.append(f"{expired} expired credential(s)")
    if near:    score += 15; flags.append(f"{near} near expiry")
    if many >= 4: score += 10; flags.append(f"{many} total credentials")
    return max(0, min(100, score)), flags

def _summarize_creds_like(app_or_sp: Dict[str, Any]) -> Dict[str, Any]:
    pw = app_or_sp.get("passwordCredentials") or []
    kc = app_or_sp.get("keyCredentials") or []
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
    return ", ".join(sorted({v for v in vals if v}))

# FAST delegated grants
def _get_all_oauth2_grants(client) -> List[Dict[str, Any]]:
    try:
        return client.get_all("oauth2PermissionGrants?$select=id,clientId,resourceId,scope,consentType,principalId")
    except Exception:
        return client.get_all("oauth2PermissionGrants")

def _group_grants_by_client(grants: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    m: Dict[str, List[Dict[str, Any]]] = {}
    for g in grants or []:
        cid = g.get("clientId")
        if not cid: continue
        m.setdefault(cid, []).append(g)
    return m

def _get_app_role_assignments_for(client, sp_id: str) -> List[Dict[str, Any]]:
    try:
        return client.get_all(f"servicePrincipals/{sp_id}/appRoleAssignments?$select=id,resourceId,principalId,appRoleId")
    except Exception:
        return client.get_all(f"servicePrincipals/{sp_id}/appRoleAssignments")

# ----------------------- Main -----------------------

def run(client, args):
    run_id = fncNewRunId("sp-audit")
    ts = datetime.now(timezone.utc).isoformat()
    deep = bool(getattr(args, "deep", False))
    fncPrintMessage(f"Running SP & App Registration Risk Audit (FAST grants; {'DEEP' if deep else 'FAST'} mode) (run={run_id})", "info")

    wip = _has_wip_license(client)

    # Fetch Service Principals (lean)
    try:
        sps = client.get_all(
            "servicePrincipals?"
            "$select=id,displayName,appId,accountEnabled,servicePrincipalType,signInAudience,verifiedPublisher,"
            "passwordCredentials,keyCredentials"
        )
    except Exception:
        sps = client.get_all("servicePrincipals")
    sp_by_appid = {sp.get("appId"): sp for sp in sps or []}
    sp_by_id    = {sp.get("id"): sp for sp in sps or []}

    # Fetch App Registrations (applications) with declared perms (RRA)
    try:
        apps = client.get_all(
            "applications?"
            "$select=id,appId,displayName,requiredResourceAccess,passwordCredentials,keyCredentials"
        )
    except Exception:
        apps = client.get_all("applications")

    # Bulk delegated grants → clientId = SP objectId
    grants_all = _get_all_oauth2_grants(client)
    grants_by_client = _group_grants_by_client(grants_all)

    findings: List[Dict[str, Any]] = []
    crit = warn = 0
    no_owners = expired_creds = multi = 0
    app_decl_hi = 0

    # --- Owners helper for SP / App ---
    def _owners_txt(kind: str, obj_id: str) -> str:
        ep = f"{'servicePrincipals' if kind=='SP' else 'applications'}/{obj_id}/owners"
        try:
            rows = client.get_all(f"{ep}?$select=id,displayName,userPrincipalName")
        except Exception:
            rows = client.get_all(ep)
        return _list_to_csv([r.get("displayName") or r.get("userPrincipalName") or r.get("id") for r in rows or []])

    # ===== Service Principals =====
    for sp in sps or []:
        sp_id = sp.get("id"); app_id = sp.get("appId")
        name  = sp.get("displayName") or app_id or sp_id
        owners = _owners_txt("SP", sp_id)
        delegated_scopes = _list_to_csv(
            sum([ (g.get("scope") or "").split() for g in grants_by_client.get(sp_id, []) ], [])
        )

        app_perm_str = ""
        if deep:
            ars = _get_app_role_assignments_for(client, sp_id)
            if ars:
                app_perm_str = "; ".join([f"resource:{a.get('resourceId','?')} appRoleId:{a.get('appRoleId','?')}" for a in ars])

        cred_state = _summarize_creds_like(sp)
        perm_hits = sum(1 for p in HIGH_IMPACT_PERMS if p in (delegated_scopes + " " + app_perm_str))
        score, flags = _score_obj(
            enabled=bool(sp.get("accountEnabled", True)),
            owners_cnt=(0 if owners == "" else len(owners.split(", "))),
            multi=sp.get("signInAudience") in ("AzureADMultipleOrgs","AzureADandPersonalMicrosoftAccount"),
            perm_hits=perm_hits,
            cred_state=cred_state
        )
        bucket = _bucket_from_score(score)

        if owners == "": no_owners += 1
        if cred_state["expired"]: expired_creds += 1
        if sp.get("signInAudience") in ("AzureADMultipleOrgs","AzureADandPersonalMicrosoftAccount"): multi += 1
        if bucket == "critical": crit += 1
        elif bucket == "warning": warn += 1

        findings.append({
            "Type": "SP",
            "Name": name,
            "Enabled": "Yes" if sp.get("accountEnabled", True) else "No",
            "Bucket": bucket,
            "RiskScore": str(score),
            "AppId": app_id or "",
            "ObjectId": sp_id or "",
            "Owners": owners or "-",
            "DelegatedGrants": delegated_scopes or "",
            "AppPermissions": (app_perm_str or ""),
            "DeclaredPerms": "",  # N/A for SPs
            "CredentialState": cred_state["text"],
            "LinkedSP": "-",      # N/A
            "Findings": "; ".join(flags) if flags else "-",
            "Mode": "Deep (includes app perms)" if deep else "Fast (delegated grants only)",
        })

    # ===== App Registrations (declared perms) =====
    for app in apps or []:
        app_id = app.get("appId"); app_obj_id = app.get("id")
        name   = app.get("displayName") or app_id or app_obj_id
        owners = _owners_txt("App", app_obj_id)
        cred_state = _summarize_creds_like(app)

        # Declared perms via requiredResourceAccess[*].resourceAccess[*].type/name/id
        declared_bits: List[str] = []
        hits = 0
        for rra in (app.get("requiredResourceAccess") or []):
            for ra in (rra.get("resourceAccess") or []):
                # Many tenants don’t return scope names here - we’ll show GUIDs when names absent
                t = (ra.get("type") or "").upper()  # Scope / Role
                n = ra.get("value") or ra.get("id") or "?"
                declared_bits.append(f"{t}:{n}")
                if isinstance(n, str) and n in HIGH_IMPACT_PERMS:
                    hits += 1

        # Linkable SP name (if exists)
        linked_sp = sp_by_appid.get(app_id)
        linked_sp_name = linked_sp.get("displayName") if isinstance(linked_sp, dict) else None

        score, flags = _score_obj(
            enabled=True,  # App reg doesn't have 'accountEnabled' - treat as enabled object controlling creds/consent
            owners_cnt=(0 if owners == "" else len(owners.split(", "))),
            multi=False,   # audience lives on SP; for AppReg we don’t know - leave false
            perm_hits=hits,
            cred_state=cred_state
        )
        bucket = _bucket_from_score(score)
        if hits: app_decl_hi += 1
        if owners == "": no_owners += 1
        if cred_state["expired"]: expired_creds += 1

        # Important UX note (your request): make it explicit we’re looking at App Registrations with permissions too
        extra_note = "App Registration declares permissions — even without tenant consent yet, risky once consented. "
        if linked_sp_name:
            extra_note += f"Linked SP: {linked_sp_name}."

        findings.append({
            "Type": "AppReg",
            "Name": name,
            "Enabled": "Yes",  # conceptual for risk surfacing
            "Bucket": bucket,
            "RiskScore": str(score),
            "AppId": app_id or "",
            "ObjectId": app_obj_id or "",
            "Owners": owners or "-",
            "DelegatedGrants": "",         # not applicable here
            "AppPermissions": "",          # consented app perms live on SP; this row is declarations
            "DeclaredPerms": ", ".join(declared_bits) if declared_bits else "",
            "CredentialState": cred_state["text"],
            "LinkedSP": linked_sp_name or "-",
            "Findings": (extra_note + (" High-impact declarations present." if hits else "")).strip(),
            "Mode": "Declared permissions (App Registration) — included because apps can have permissions too.",
        })

    # ---------- Console preview ----------
    headers = ["Type","Name","Enabled","Bucket","RiskScore"]
    keep = headers + ["AppId","ObjectId","Owners","DelegatedGrants","AppPermissions","DeclaredPerms","CredentialState","LinkedSP","Findings","Mode"]
    safe_rows = _sanitize_table(findings, keep)
    safe_rows.sort(key=lambda r: int(r.get("RiskScore") or 0), reverse=True)

    fncPrintMessage("Apps & Service Principals — top 25 by risk", "info")
    print(fncToTable(safe_rows[:25], headers=headers, max_rows=min(25, len(safe_rows))))

    total = len(safe_rows)
    ok_count = total - crit - warn

    kpis = [
        {"label":"Total (Apps + SPs)","value":str(total),"tone":"primary","icon":"bi-diagram-3"},
        {"label":"Critical","value":str(crit),"tone":"danger","icon":"bi-exclamation-octagon"},
        {"label":"Warning","value":str(warn),"tone":"warning","icon":"bi-exclamation-triangle"},
        {"label":"No Owners","value":str(no_owners),"tone":"secondary","icon":"bi-person-x"},
        {"label":"Expired Creds","value":str(expired_creds),"tone":"secondary","icon":"bi-clock-history"},
        {"label":"Multi-tenant SPs","value":str(multi),"tone":"secondary","icon":"bi-people"},
        {"label":"AppRegs w/ High-Impact Declarations","value":str(app_decl_hi),"tone":"info","icon":"bi-patch-exclamation"},
        {"label":"WIP License","value":("Present" if wip["present"] else "Not detected"),"tone":"info","icon":"bi-patch-check" if wip["present"] else "bi-patch-exclamation"},
    ]

    charts = {
        "place": "summary",
        "severity": {"labels": ["Critical","Warning","OK"], "data":[crit, warn, ok_count]},
        "mix": {"labels": ["Service Principals","App Registrations"], "data":[sum(1 for r in safe_rows if r["Type"]=="SP"), sum(1 for r in safe_rows if r["Type"]=="AppReg")]},
    }

    standouts = {}
    if safe_rows:
        top = safe_rows[0]
        standouts["group"] = {
            "title":"Highest-Risk Object",
            "name": f"{top['Type']}: {top['Name']}",
            "risk_score": float(min(10.0, (int(top['RiskScore'])/10.0))),
            "comment": f"{(top['Findings'] or '')[:140]}{'…' if (top['Findings'] or '') and len(top['Findings'])>140 else ''}"
        }

    data = {
        "provider": "entra",
        "run_id": run_id,
        "timestamp": ts,

        "sp_findings": safe_rows,
        "wip_licensing": wip,

        "_kpis": kpis,
        "_standouts": standouts,
        "_charts": charts,

        "_title": "App & Service Principal Risk Audit",
        "_subtitle": "FAST delegated grants + AppReg declared permissions (use --deep to include SP app permissions)",
        "_container_class": "sp-audit",
        "_inline_css": SP_CSS,
        "_inline_js":  SP_JS,
    }

    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "sp_risk_audit", data)

    fncPrintMessage("SP & App Registration Risk Audit complete.", "success")
    return data
