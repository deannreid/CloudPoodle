# ================================================================
# File     : modules/entra/pim_role_audit.py
# Purpose  : Entra PIM (Privileged Identity Management) Role Audit
# ================================================================

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
import requests

from core.utils import (
    fncPrintMessage,
    fncToTable,
    fncNewRunId,
)
from core.reporting import fncWriteHTMLReport

REQUIRED_PERMS = [
    "Directory.Read.All",
    "RoleManagement.Read.Directory",
]

# ----------------------- Module-local CSS/JS ---------------------

PIM_CSS = r"""
/* Clickable rows + drawer */
.pim-audit .pa-clickable { cursor: pointer; }
.pim-audit .pa-clickable:hover { background: rgba(255,255,255,.05); }
.pim-audit .pa-expander > td { padding: 0; background: #10141b; }
.pim-audit .pa-expander-body { padding: 14px 16px; border-top: 1px solid rgba(255,255,255,.08); }

/* Keep overview columns tight; allow hiding */
.pim-audit table th,
.pim-audit table td { white-space: nowrap; }
.pim-audit .pa-hide { display: none !important; }

/* Chevron in principalName */
.pim-audit .pa-name { display:inline-flex; align-items:center; gap:8px; }
.pim-audit .pa-chevron { display:inline-block; width:1em; transition: transform .15s ease; opacity:.85; }
.pim-audit .pa-open .pa-chevron { transform: rotate(90deg); }

/* Drawer layout */
.pim-audit .pa-flex { display: grid; grid-template-columns: 1fr 1.2fr; gap: 16px; }
@media (max-width: 1100px){ .pim-audit .pa-flex { grid-template-columns: 1fr; } }
.pim-audit .pa-pane {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: color-mix(in srgb, var(--card) 94%, #000 6%);
  overflow: hidden;
}
.pim-audit .pa-pane h5 {
  margin: 0; padding: 10px 12px;
  background: color-mix(in srgb, var(--accent2) 85%, #000 15%);
  color: #fff; font-weight: 700; border-bottom: 1px solid rgba(0,0,0,.2);
}
.pim-audit .pa-kv { width: 100%; border-collapse: collapse; }
.pim-audit .pa-kv th, .pim-audit .pa-kv td {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top;
}
.pim-audit .pa-kv th {
  width: 220px; white-space: nowrap;
  background: color-mix(in srgb, var(--accent2) 12%, var(--card)); font-weight: 600;
}
.pim-audit .pa-kv tr:last-child td, .pim-audit .pa-kv tr:last-child th { border-bottom: 0; }

/* Toolbar (search + view more) */
.pim-audit .pa-toolbar{
  display:flex; gap:10px; align-items:center; margin:6px 2px 0 2px; flex-wrap:wrap;
}
.pim-audit .pa-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:220px; outline:none;
}
.pim-audit .pa-toolbar .btn{
  padding:6px 12px; border:1px solid var(--border); border-radius:999px;
  background:var(--card); cursor:pointer; font-weight:600;
}
.pim-audit .pa-toolbar .btn.primary{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}

/* Sticky headers + wrapped JSON in details cells */
.pim-audit table thead th{ position:sticky; top:0; z-index:2 }
.pim-audit .cp-json, .pim-audit .cp-json pre, .pim-audit .cp-json code {
  white-space: pre-wrap !important; word-break: break-word !important; overflow-x: hidden !important;
}
"""


PIM_JS = r"""
(function () {
  const root = document.querySelector('.pim-audit') || document;

  // Known table ids from reporting.py (section keys → ids)
  const permTbl = root.querySelector('#tbl-permanent-assignments');
  const actTbl  = root.querySelector('#tbl-active-assignments');
  const eliTbl  = root.querySelector('#tbl-eligible-assignments');
  if (!permTbl && !actTbl && !eliTbl) return;

  // ---- helpers ----
  const txt = el => (el?.textContent || '').trim();

  function hideColumns(table, keepSet) {
    // Always hide these if present (we use them inside the drawer)
    const HIDE_ALWAYS = new Set(['source', 'roleId']);
    const ths = Array.from(table.querySelectorAll('thead th'));
    const idxByName = new Map();
    ths.forEach((th,i)=> idxByName.set((th.textContent || '').trim(), i));

    ths.forEach((th,i)=>{
      const name = (th.textContent || '').trim();
      if (HIDE_ALWAYS.has(name.toLowerCase()) || !keepSet.has(name)) {
        table
          .querySelectorAll(`thead th:nth-child(${i+1}), tbody td:nth-child(${i+1})`)
          .forEach(c => c.classList.add('pa-hide'));
      }
    });

    return idxByName;
  }

  function attachRowDrawer(table, options){
    const { idCol, nameCol } = options;
    const headCells = Array.from(table.querySelectorAll('thead th'));
    const totalCols = headCells.length;

    // Build a header map to pull cell values by header name
    const headers = headCells.map(h=>txt(h));

    Array.from(table.querySelectorAll('tbody tr')).forEach(tr=>{
      const nameCell = nameCol >= 0 ? tr.children[nameCol] : tr.children[0];
      if (!nameCell) return;
      const label = txt(nameCell);
      nameCell.innerHTML = `<span class="pa-name"><span class="pa-chevron">▸</span><span class="pa-label"></span></span>`;
      nameCell.querySelector('.pa-label').textContent = label;

      tr.classList.add('pa-clickable');
      tr.addEventListener('click', ()=>{
        const open = tr.classList.contains('pa-open');
        const next = tr.nextElementSibling;
        if (next && next.classList.contains('pa-expander')) next.remove();
        tr.classList.remove('pa-open');
        if (open) return;

        // Collect row data into a map {Header: innerHTML}
        const cells = Array.from(tr.children);
        const row = {};
        headers.forEach((h, i) => row[h] = cells[i] ? cells[i].innerHTML : '');

        // Pull common fields (handle header case variations)
        const pick = (k) => row[k] || row[k.toLowerCase()] || row[k.toUpperCase()] || '';
        const pname  = pick('principalName') || label;
        const ptype  = pick('principalType') || '';
        const rname  = pick('roleName') || '';
        const rid    = pick('roleId') || '';
        const start  = pick('start') || '-';
        const end    = pick('end') || '-';
        const status = pick('status') || '';
        const risk   = pick('risk') || '';
        const bucket = pick('bucket') || '';
        const details= pick('Details') || '';

        const html = `
          <div class="pa-expander-body">
            <div class="pa-flex">
              <div class="pa-pane">
                <h5>Overview</h5>
                <table class="pa-kv"><tbody>
                  <tr><th>Principal</th><td>${pname}</td></tr>
                  <tr><th>Type</th><td>${ptype}</td></tr>
                  <tr><th>Role</th><td>${rname}</td></tr>
                  <tr><th>Role Id</th><td>${rid}</td></tr>
                  <tr><th>Status</th><td>${status}</td></tr>
                  <tr><th>Risk / Bucket</th><td>${risk} / ${bucket}</td></tr>
                  <tr><th>Window</th><td>${start} → ${end}</td></tr>
                </tbody></table>
              </div>
              <div class="pa-pane">
                <h5>Details</h5>
                <table class="pa-kv"><tbody>
                  <tr><th>Full Object</th><td>${details || '<em>none available</em>'}</td></tr>
                </tbody></table>
              </div>
            </div>
          </div>`;

        const exp = document.createElement('tr');
        const td  = document.createElement('td');
        exp.className = 'pa-expander';
        td.colSpan = totalCols;
        td.innerHTML = html;
        exp.appendChild(td);
        tr.parentNode.insertBefore(exp, tr.nextSibling);
        tr.classList.add('pa-open');

        const chev = tr.querySelector('.pa-chevron');
        if (chev) chev.textContent = '▾';
      });
    });
  }

  function addToolbar(table, title){
    const card = table.closest('.card');
    if (!card) return null;
    const bar = document.createElement('div');
    bar.className = 'pa-toolbar';
    bar.innerHTML = `
      <input type="search" placeholder="Search ${title}…" aria-label="Search ${title}">
      <button class="btn primary" data-action="viewmore" style="display:none">View more…</button>
    `;
    card.insertBefore(bar, card.querySelector('.tablewrap'));
    return bar;
  }

  function paginateAndSearch(table, toolbar){
    const search = toolbar.querySelector('input[type="search"]');
    const viewMoreBtn = toolbar.querySelector('[data-action="viewmore"]');
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    const PAGE = 20; let expanded = false;

    function apply(){
      const q = (search.value||'').toLowerCase();
      let shown = 0;
      rows.forEach((tr)=>{
        const match = !q || tr.textContent.toLowerCase().includes(q);
        if (!match) { tr.style.display = 'none'; return; }
        if (!expanded && q === '' && shown >= PAGE) { tr.style.display = 'none'; return; }
        tr.style.display = ''; shown++;
      });
      const moreExists = rows.filter(tr => tr.style.display !== 'none').length < rows.length && q === '' && !expanded;
      viewMoreBtn.style.display = moreExists ? '' : 'none';
    }

    apply();
    search.addEventListener('input', apply);
    viewMoreBtn.addEventListener('click', ()=>{ expanded = true; apply(); });
  }

  function wireTable(tbl, title){
    if (!tbl) return;
    // Keep only these columns in the grid; “Details” remains for the drawer
    const keep = new Set(['principalName','principalType','roleName','start','end','status','risk','bucket','Details']);
    // Build an index map and hide others
    const idx = (function hide(){
      const ths = Array.from(tbl.querySelectorAll('thead th'));
      const idxByName = new Map();
      ths.forEach((th,i)=> idxByName.set((th.textContent || '').trim(), i));
      ths.forEach((th,i)=>{
        const name = (th.textContent || '').trim();
        const k = name.toLowerCase();
        if (!keep.has(name) && !keep.has(k)) {
          tbl.querySelectorAll(`thead th:nth-child(${i+1}), tbody td:nth-child(${i+1})`)
             .forEach(c => c.classList.add('pa-hide'));
        }
      });
      return idxByName;
    })();

    // Determine index of principalName for the chevron label
    const nameIdx = (()=>{
      for (const cand of ['principalName','PrincipalName','principalname']) {
        if (idx.has(cand)) return idx.get(cand);
      }
      return 0;
    })();

    attachRowDrawer(tbl, { idCol: null, nameCol: nameIdx });

    const bar = addToolbar(tbl, title);
    if (bar) paginateAndSearch(tbl, bar);
  }

  wireTable(permTbl, 'Permanent Assignments');
  wireTable(actTbl,  'Active Assignments');
  wireTable(eliTbl,  'Eligible Assignments');
})();
"""


# ----------------------- Helpers -----------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _client_headers(client) -> Dict[str,str]:
    # Try common attributes; fall back to token
    hdrs = getattr(client, "headers", None) or getattr(client, "_headers", None)
    if isinstance(hdrs, dict):
        return hdrs
    token = getattr(client, "token", "") or getattr(client, "_token", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

def _client_handle_response(client):
    # Support both older and newer client implementations
    return getattr(client, "_handle_response", None) or getattr(client, "fncHandleResponse", None)

def _safe_get(obj: Dict, *path, default=None):
    cur = obj
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

OR_LIMIT = 15  # Graph limit for OR'd child clauses

def _post_get_by_ids(client, ids, types):
    """
    Preferred: POST /directoryObjects/getByIds
    """
    if not ids:
        return []
    url = "https://graph.microsoft.com/v1.0/directoryObjects/getByIds"
    payload = {"ids": list(dict.fromkeys(ids)), "types": types}

    handler = _client_handle_response(client)
    resp = requests.post(url, headers=_client_headers(client), json=payload)
    data = handler(resp) if handler else resp.json()
    return data.get("value", []) if isinstance(data, dict) else []

def _get_role_definitions(client) -> Dict[str, Dict[str, Any]]:
    try:
        rows = client.get_all("roleManagement/directory/roleDefinitions?$select=id,displayName,isBuiltIn")
    except Exception:
        rows = client.get_all("roleManagement/directory/roleDefinitions")
    out = {}
    for r in rows or []:
        out[r.get("id")] = {
            "displayName": r.get("displayName") or "(unknown role)",
            "isBuiltIn": bool(r.get("isBuiltIn")),
        }
    return out

def _get_permanent_role_assignments(client) -> List[Dict[str, Any]]:
    try:
        url = "roleManagement/directory/roleAssignments?$select=id,principalId,roleDefinitionId,directoryScopeId"
        return client.get_all(url)
    except Exception as ex:
        fncPrintMessage(f"Role assignments $select failed; retrying without $select ({ex})", "warn")
        return client.get_all("roleManagement/directory/roleAssignments")

def _get_assignment_schedule_instances(client) -> List[Dict[str, Any]]:
    try:
        url = ("roleManagement/directory/roleAssignmentScheduleInstances"
               "?$select=id,principalId,roleDefinitionId,startDateTime,endDateTime,directoryScopeId")
        return client.get_all(url)
    except Exception:
        return client.get_all("roleManagement/directory/roleAssignmentScheduleInstances")

def _get_eligibility_schedule_instances(client) -> List[Dict[str, Any]]:
    try:
        url = ("roleManagement/directory/roleEligibilityScheduleInstances"
               "?$select=id,principalId,roleDefinitionId,startDateTime,endDateTime,directoryScopeId")
        return client.get_all(url)
    except Exception:
        return client.get_all("roleManagement/directory/roleEligibilityScheduleInstances")

# --------- principal hydration (users, groups, sps) ----------

def _batch_get_users(client, ids):
    out = {}
    if not ids:
        return out
    ids = list(dict.fromkeys(i for i in ids if i))

    # Preferred: POST getByIds
    try:
        objs = _post_get_by_ids(client, ids, ["user"])
        for o in objs:
            if isinstance(o, dict) and (o.get("@odata.type","").lower().endswith("user") or "userPrincipalName" in o):
                out[o["id"]] = {
                    "id": o.get("id"),
                    "displayName": o.get("displayName"),
                    "userPrincipalName": o.get("userPrincipalName"),
                    "userType": o.get("userType"),
                    "accountEnabled": o.get("accountEnabled"),
                }
        if len(out) == len(ids):
            return out
    except Exception as ex:
        fncPrintMessage(f"getByIds (users) failed, will chunk GETs: {ex}", "warn")

    # Fallback: GET in OR chunks (≤15)
    for i in range(0, len(ids), OR_LIMIT):
        chunk = ids[i:i+OR_LIMIT]
        flt = " or ".join([f"id eq '{cid}'" for cid in chunk])
        try:
            rows = client.get_all(
                "users?$select=id,displayName,userPrincipalName,userType,accountEnabled"
                f"&$filter={flt}"
            )
            for r in rows or []:
                out[r["id"]] = r
        except Exception as ex:
            fncPrintMessage(f"User chunk fetch failed: {ex}", "warn")
    return out

def _batch_get_sps(client, ids):
    out = {}
    if not ids:
        return out
    ids = list(dict.fromkeys(i for i in ids if i))

    # Preferred: POST getByIds
    try:
        objs = _post_get_by_ids(client, ids, ["servicePrincipal"])
        for o in objs:
            if isinstance(o, dict) and (o.get("@odata.type","").lower().endswith("serviceprincipal") or "appId" in o):
                out[o["id"]] = {
                    "id": o.get("id"),
                    "displayName": o.get("displayName"),
                    "servicePrincipalType": o.get("servicePrincipalType"),
                    "publisherName": o.get("publisherName"),
                    "appId": o.get("appId"),
                    "accountEnabled": o.get("accountEnabled"),
                }
        if len(out) == len(ids):
            return out
    except Exception as ex:
        fncPrintMessage(f"getByIds (servicePrincipals) failed, will chunk GETs: {ex}", "warn")

    # Fallback: GET in OR chunks (≤15)
    for i in range(0, len(ids), OR_LIMIT):
        chunk = ids[i:i+OR_LIMIT]
        flt = " or ".join([f"id eq '{cid}'" for cid in chunk])
        try:
            rows = client.get_all(
                "servicePrincipals?"
                "$select=id,displayName,servicePrincipalType,publisherName,appId,accountEnabled"
                f"&$filter={flt}"
            )
            for r in rows or []:
                out[r["id"]] = r
        except Exception as ex:
            fncPrintMessage(f"SP chunk fetch failed: {ex}", "warn")
    return out

def _batch_get_groups(client, ids):
    out = {}
    if not ids:
        return out
    ids = list(dict.fromkeys(i for i in ids if i))

    # Preferred: POST getByIds
    try:
        objs = _post_get_by_ids(client, ids, ["group"])
        for o in objs:
            if not isinstance(o, dict) or "id" not in o:
                continue
            out[o["id"]] = {
                "id": o.get("id"),
                "displayName": o.get("displayName"),
                "securityEnabled": o.get("securityEnabled"),
                "groupTypes": o.get("groupTypes") or [],
                "visibility": o.get("visibility"),
            }
        if len(out) == len(ids):
            return out
    except Exception as ex:
        fncPrintMessage(f"getByIds (groups) failed, will chunk GETs: {ex}", "warn")

    # Fallback: GET in OR chunks (≤15)
    for i in range(0, len(ids), OR_LIMIT):
        chunk = ids[i:i+OR_LIMIT]
        flt = " or ".join([f"id eq '{cid}'" for cid in chunk])
        try:
            rows = client.get_all(
                "groups?$select=id,displayName,securityEnabled,groupTypes,visibility"
                f"&$filter={flt}"
            )
            for r in rows or []:
                out[r["id"]] = r
        except Exception as ex:
            fncPrintMessage(f"Group chunk fetch failed: {ex}", "warn")
    return out

def _hydrate_principals(client, principal_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    principal_ids = list(dict.fromkeys(principal_ids))
    users  = _batch_get_users(client, principal_ids)
    left   = [i for i in principal_ids if i not in users]
    groups = _batch_get_groups(client, left)
    left   = [i for i in left if i not in groups]
    sps    = _batch_get_sps(client, left)

    out = {}
    for k, v in users.items():
        out[k] = {"type": "User", "name": v.get("displayName") or v.get("userPrincipalName") or k, "raw": v}
    for k, v in groups.items():
        out[k] = {"type": "Group", "name": v.get("displayName") or k, "raw": v}
    for k, v in sps.items():
        out[k] = {"type": "ServicePrincipal", "name": v.get("displayName") or k, "raw": v}
    for k in principal_ids:
        out.setdefault(k, {"type": "Unknown", "name": k, "raw": {"id": k}})
    return out

# ---- Risk heuristics ----

CRITICAL_ROLES = {
    "Global Administrator",
    "Privileged Role Administrator",
    "User Administrator",
    "Application Administrator",
    "Cloud Application Administrator",
    "Security Administrator",
}

def _score_assignment(role_name: str, prin_type: str, eligible: bool) -> int:
    base = 10 if role_name in CRITICAL_ROLES else 4
    if prin_type == "Group":
        base += 4
    if eligible:
        base = max(2, base - 3)
    return min(100, base * 6)

def _bucket_from_risk(r: int) -> str:
    if r is None: return "unknown"
    if r >= 80: return "critical"
    if r >= 40: return "warning"
    return "ok"

# ----------------------- Main -----------------------

def run(client, args):
    run_id = fncNewRunId("pimaudit")
    ts = _iso_now()
    fncPrintMessage(f"Running PIM Role Audit (run={run_id})", "info")

    defs = _get_role_definitions(client)
    perms = _get_permanent_role_assignments(client) or []
    active = _get_assignment_schedule_instances(client) or []
    elig   = _get_eligibility_schedule_instances(client) or []

    principal_ids = [r.get("principalId") for r in (perms + active + elig) if r.get("principalId")]
    prin_map = _hydrate_principals(client, principal_ids)

    perm_rows: List[Dict[str, Any]] = []
    act_rows:  List[Dict[str, Any]] = []
    eli_rows:  List[Dict[str, Any]] = []

    def _mk(role_id, pid, start=None, end=None, kind="Active", src=None, eligible=False):
        rmeta = defs.get(role_id, {})
        pinfo = prin_map.get(pid, {}) or {}
        pname = pinfo.get("name") or pid
        ptype = pinfo.get("type") or "Unknown"
        rname = rmeta.get("displayName") or role_id
        risk  = _score_assignment(rname, ptype, eligible)
        buck  = _bucket_from_risk(risk)

        # Details dict -> pretty JSON dropdown in report
        details = {
            "source": src or {},
            "principal": pinfo.get("raw", {"id": pid}),
            "role": {"id": role_id, "displayName": rname},
            "window": {"start": start or None, "end": end or None, "status": kind},
            "computed": {"risk": risk, "bucket": buck},
        }

        return {
            "principalName": pname,
            "principalType": ptype,
            "roleName": rname,
            "roleId": role_id,
            "start": start or "-",
            "end": end or "-",
            "status": kind,
            "risk": risk,
            "bucket": buck,
            "Details": details,     # << dropdown-friendly column
            "source": src or {},    # kept for completeness; hidden in JS
        }

    for r in perms:
        perm_rows.append(_mk(r.get("roleDefinitionId"), r.get("principalId"),
                             kind="Permanent", src=r, eligible=False))

    for r in active:
        act_rows.append(_mk(r.get("roleDefinitionId"), r.get("principalId"),
                            start=r.get("startDateTime"), end=r.get("endDateTime"),
                            kind="Active", src=r, eligible=False))

    for r in elig:
        eli_rows.append(_mk(r.get("roleDefinitionId"), r.get("principalId"),
                            start=r.get("startDateTime"), end=r.get("endDateTime"),
                            kind="Eligible", src=r, eligible=True))

    # Sort by risk desc
    perm_rows.sort(key=lambda x: x.get("risk",0), reverse=True)
    act_rows.sort(key=lambda x: x.get("risk",0), reverse=True)
    eli_rows.sort(key=lambda x: x.get("risk",0), reverse=True)

    # Console previews
    if perm_rows:
        fncPrintMessage("Permanent Role Assignments (top 20 by risk)", "info")
        print(fncToTable(perm_rows[:20], headers=["principalName","principalType","roleName","risk"], max_rows=20))
    if act_rows:
        fncPrintMessage("PIM Active Assignments (top 20 by risk)", "info")
        print(fncToTable(act_rows[:20], headers=["principalName","principalType","roleName","start","end","risk"], max_rows=20))
    if eli_rows:
        fncPrintMessage("PIM Eligible Assignments (top 20 by risk)", "info")
        print(fncToTable(eli_rows[:20], headers=["principalName","principalType","roleName","start","end","risk"], max_rows=20))

    # KPIs
    total_perm = len(perm_rows)
    total_active = len(act_rows)
    total_elig = len(eli_rows)

    def _count_crit(rows): return sum(1 for r in rows if r.get("bucket")=="critical")
    def _count_warn(rows): return sum(1 for r in rows if r.get("bucket")=="warning")

    all_rows = perm_rows + act_rows + eli_rows

    kpis = [
        {"label":"Permanent Assignments","value":str(total_perm),"tone":"danger","icon":"bi-shield-lock"},
        {"label":"PIM Active","value":str(total_active),"tone":"warning","icon":"bi-lightning-charge"},
        {"label":"PIM Eligible","value":str(total_elig),"tone":"secondary","icon":"bi-hourglass-split"},
        {"label":"Critical (All)","value":str(_count_crit(all_rows)),"tone":"danger","icon":"bi-exclamation-octagon"},
        {"label":"Warning (All)","value":str(_count_warn(all_rows)),"tone":"warning","icon":"bi-exclamation-triangle"},
    ]

    # Standouts
    standouts = {}
    top_perm = perm_rows[0] if perm_rows else None
    top_active = act_rows[0] if act_rows else None
    top_elig = eli_rows[0] if eli_rows else None
    if top_perm:
        standouts["group"] = {
            "title":"Highest-Risk Permanent",
            "name": f"{top_perm['principalName']} → {top_perm['roleName']}",
            "risk_score": float(min(10.0, top_perm["risk"]/10.0)),
            "comment": f"{top_perm['principalType']} / {top_perm['bucket'].title()}",
        }
    if top_active:
        standouts["user"] = {
            "title":"Highest-Risk Active (PIM)",
            "name": f"{top_active['principalName']} → {top_active['roleName']}",
            "risk_score": float(min(10.0, top_active["risk"]/10.0)),
            "comment": f"Active until {top_active['end'] or '-'}",
        }
    if top_elig:
        standouts["computer"] = {
            "title":"Highest-Risk Eligible (PIM)",
            "name": f"{top_elig['principalName']} → {top_elig['roleName']}",
            "risk_score": float(min(10.0, top_elig["risk"]/10.0)),
            "comment": f"Eligible window ends {top_elig['end'] or '-'}",
        }

    # Chart
    b_counts = {"critical":0,"warning":0,"ok":0,"unknown":0}
    for r in all_rows:
        b_counts[r.get("bucket","unknown")] = b_counts.get(r.get("bucket","unknown"),0)+1
    chart_labels = ["Critical","Warning","OK","Unknown"]
    chart_values = [b_counts["critical"], b_counts["warning"], b_counts["ok"], b_counts["unknown"]]

    data = {
        "provider": "entra",
        "run_id": run_id,
        "timestamp": ts,
        "summary": {
            "Permanent Assignments": total_perm,
            "PIM Active Assignments": total_active,
            "PIM Eligible Assignments": total_elig,
            "Critical (All)": _count_crit(all_rows),
            "Warning (All)": _count_warn(all_rows),
        },

        # Tables
        "permanent_assignments": perm_rows,
        "active_assignments": act_rows,
        "eligible_assignments": eli_rows,

        # Dashboard
        "_kpis": kpis,
        "_standouts": standouts,
        "_charts": {
            "place": "summary",
            "severity": {"labels": chart_labels, "data": chart_values},
        },

        "_title": "PIM Role Audit",
        "_subtitle": "Permanent, Active, and Eligible privileged role assignments",
        "_container_class": "pim-audit",
        "_inline_css": PIM_CSS,
        "_inline_js":  PIM_JS,
    }

    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "pim_role_audit", data)

    fncPrintMessage("PIM Role Audit module complete.", "success")
    return data
