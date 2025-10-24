# ================================================================
# File     : modules/entra/priv_esc_pathing.py
# Purpose  : Detect potential Entra ID privilege escalation paths
#            (PIM eligible/active, permanent admin, group-based,
#             owners of role-assignable groups, role mgmt roles,
#             “can self-assign GA” candidates, and owner self-assign ops)
# Notes    : Read-only. All v1.0 endpoints. Robust to Graph quirks.
# Output   : data["findings"]                    -> list[dict]
#            data["ga_self_path_candidates"]     -> list[dict]
#            data["owner_membership_issues"]     -> list[dict]
#            data["owner_self_assign_ops"]       -> list[dict]
#            plus _kpis, _charts for dashboard
# ================================================================

### WIP

from datetime import datetime, timezone
from typing import Dict, Any, List, Iterable

from core.utils import (
    fncPrintMessage,
    fncToTable,
    fncNewRunId,
)
from core.reporting import fncWriteHTMLReport

REQUIRED_PERMS = [
    "Directory.Read.All",
    "RoleManagement.Read.Directory",
    "Group.Read.All",
]

PE_CSS = r"""
.priv-esc table { table-layout:auto }
.priv-esc table thead th{ position:sticky; top:0; z-index:2 }

/* Toolbar (search + view more) */
.priv-esc .pe-toolbar{
  display:flex; gap:10px; flex-wrap:wrap; align-items:center;
  margin:6px 2px 0 2px;
}
.priv-esc .pe-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:260px; outline:none;
}
.priv-esc .pe-toolbar .btn{
  padding:6px 12px; border:1px solid var(--border); border-radius:999px;
  background:var(--card); cursor:pointer; font-weight:600;
}
.priv-esc .pe-toolbar .btn.primary{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}

/* Drawer styles (like CA audit) */
.priv-esc .pe-clickable{ cursor:pointer }
.priv-esc .pe-clickable:hover{ background:rgba(255,255,255,.05) }
.priv-esc .pe-expander > td{ padding:0; background:color-mix(in srgb, var(--card) 92%, #000 8%) }
.priv-esc .pe-expander-body{ padding:14px 16px; border-top:1px solid var(--border) }

/* Info panes inside drawers */
.priv-esc .pe-flex{ display:grid; grid-template-columns: 1fr 1.2fr; gap:16px }
@media (max-width: 1100px){ .priv-esc .pe-flex{ grid-template-columns: 1fr } }
.priv-esc .pe-pane{
  border:1px solid var(--border); border-radius:10px;
  background:color-mix(in srgb, var(--card) 94%, #000 6%); overflow:hidden;
}
.priv-esc .pe-pane h5{
  margin:0; padding:10px 12px;
  background:color-mix(in srgb, var(--accent2) 85%, #000 15%);
  color:#fff; font-weight:700; border-bottom:1px solid rgba(0,0,0,.2);
}
.priv-esc .pe-kv{ width:100%; border-collapse:collapse }
.priv-esc .pe-kv th,.priv-esc .pe-kv td{
  text-align:left; padding:8px 12px; border-bottom:1px solid var(--border); vertical-align:top;
}
.priv-esc .pe-kv th{
  width:210px; white-space:nowrap;
  background:color-mix(in srgb, var(--accent2) 12%, var(--card)); font-weight:600;
}
.priv-esc .pe-kv tr:last-child td,.priv-esc .pe-kv tr:last-child th{ border-bottom:0 }

/* Wrap JSON/long text */
.priv-esc .cp-json, .priv-esc .cp-json pre, .priv-esc .cp-json code{
  white-space:pre-wrap !important; word-break:break-word !important; overflow-x:hidden !important;
}
"""

PE_JS = r"""
(function(){
  const root = document.querySelector('.priv-esc') || document;

  function addToolbar(table, title){
    const card = table.closest('.card');
    if (!card) return null;
    const bar = document.createElement('div');
    bar.className = 'pe-toolbar';
    bar.innerHTML = `
      <input type="search" placeholder="Search ${title}…" aria-label="Search ${title}">
      <button class="btn primary" data-action="viewmore" style="display:none">View more…</button>
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

  function attachRowDrawer(table, build){
    if(!table) return;
    const headCells = Array.from(table.querySelectorAll('thead th')).map(h=> (h.textContent||'').trim());
    const totalCols = headCells.length;

    Array.from(table.querySelectorAll('tbody tr')).forEach(tr=>{
      tr.classList.add('pe-clickable');
      tr.addEventListener('click', ()=>{
        const open = tr.classList.contains('pe-open');
        const next = tr.nextElementSibling;
        if (next && next.classList.contains('pe-expander')) next.remove();
        tr.classList.remove('pe-open');
        if (open) return;

        // Collect row data
        const row = {};
        headCells.forEach((name, idx)=>{
          const td = tr.children[idx];
          row[name] = td ? td.innerHTML : '';
        });

        // Build drawer body
        const html = build(row);

        // Insert
        const exp = document.createElement('tr');
        const td  = document.createElement('td');
        exp.className = 'pe-expander';
        td.colSpan = totalCols;
        td.innerHTML = html;
        exp.appendChild(td);
        tr.parentNode.insertBefore(exp, tr.nextSibling);
        tr.classList.add('pe-open');
      });
    });
  }

  function enhance(tableId, title, buildDrawer){
    const tbl = root.querySelector('#'+tableId);
    if(!tbl) return;
    const bar = addToolbar(tbl, title);
    if (bar) paginateAndSearch(tbl, bar, 20);
    if (buildDrawer) attachRowDrawer(tbl, buildDrawer);
  }

  // Drawer builders
  const buildFindingsDrawer = (row)=>`
    <div class="pe-expander-body">
      <div class="pe-flex">
        <div class="pe-pane">
          <h5>Overview</h5>
          <table class="pe-kv"><tbody>
            <tr><th>User</th><td>${row['User']||'-'}</td></tr>
            <tr><th>Path</th><td>${row['EscalationPath']||'-'}</td></tr>
            <tr><th>Role</th><td>${row['Role']||'-'}</td></tr>
            <tr><th>Type</th><td>${row['PrincipalType']||'-'}</td></tr>
            <tr><th>Flavor</th><td>${row['Flavor']||'-'}</td></tr>
            <tr><th>Risk</th><td>${row['RiskScore']||'-'}</td></tr>
          </tbody></table>
        </div>
        <div class="pe-pane">
          <h5>Details</h5>
          <table class="pe-kv"><tbody>
            <tr><th>Notes</th><td class="cp-json">${row['Details']||'<em>none</em>'}</td></tr>
          </tbody></table>
        </div>
      </div>
    </div>`;

  const buildOwnerMembersDrawer = (row)=>`
    <div class="pe-expander-body">
      <div class="pe-flex">
        <div class="pe-pane">
          <h5>Owner & Group</h5>
          <table class="pe-kv"><tbody>
            <tr><th>Group</th><td>${row['GroupName']||'-'} (${row['GroupId']||'-'})</td></tr>
            <tr><th>Owner</th><td>${row['OwnerName']||'-'} (${row['OwnerId']||'-'})</td></tr>
            <tr><th>Issue</th><td>${row['Issue']||'-'}</td></tr>
          </tbody></table>
        </div>
        <div class="pe-pane">
          <h5>Privileged Roles</h5>
          <table class="pe-kv"><tbody>
            <tr><th>Roles</th><td class="cp-json">${row['PrivilegedRoles']||'<em>unknown</em>'}</td></tr>
            <tr><th>Details</th><td class="cp-json">${row['Details']||'<em>none</em>'}</td></tr>
          </tbody></table>
        </div>
      </div>
    </div>`;

  const buildOwnerSelfAssignDrawer = (row)=>`
    <div class="pe-expander-body">
      <div class="pe-flex">
        <div class="pe-pane">
          <h5>Group → Owner</h5>
          <table class="pe-kv"><tbody>
            <tr><th>Group</th><td>${row['GroupName']||'-'} (${row['GroupId']||'-'})</td></tr>
            <tr><th>Owner</th><td>${row['OwnerName']||'-'} (${row['OwnerId']||'-'})</td></tr>
            <tr><th>Self-assign Enabled?</th><td>${row['SelfAssignEnabled']||'-'}</td></tr>
            <tr><th>Reason</th><td class="cp-json">${row['Reason']||'-'}</td></tr>
          </tbody></table>
        </div>
        <div class="pe-pane">
          <h5>Roles Owner Can Gain</h5>
          <table class="pe-kv"><tbody>
            <tr><th>Privileged Roles</th><td class="cp-json">${row['PrivilegedRoles']||'<em>unknown</em>'}</td></tr>
            <tr><th>Details</th><td class="cp-json">${row['Details']||'<em>—</em>'}</td></tr>
          </tbody></table>
        </div>
      </div>
    </div>`;

  // Hook up each table
  enhance('tbl-findings','Findings', buildFindingsDrawer);
  enhance('tbl-ga-self-path-candidates','GA Self-path Candidates', buildFindingsDrawer);
  enhance('tbl-owner-membership-issues','Owners also Members (Privileged Groups)', buildOwnerMembersDrawer);
  enhance('tbl-owner-self-assign-ops','Owner Self-Assign Opportunities', buildOwnerSelfAssignDrawer);
})();
"""

OR_LIMIT = 15  # Graph OR-clause child limit

# ---------------- helpers ----------------

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

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _get_role_definitions(client) -> Dict[str, Dict[str, Any]]:
    try:
        rows = client.get_all("roleManagement/directory/roleDefinitions?$select=id,displayName,isBuiltIn")
    except Exception:
        rows = client.get_all("roleManagement/directory/roleDefinitions")
    out = {}
    for r in rows or []:
        out[r.get("id")] = {"displayName": r.get("displayName") or "(unknown role)", "isBuiltIn": bool(r.get("isBuiltIn"))}
    return out

def _get_permanent_role_assignments(client) -> List[Dict[str, Any]]:
    try:
        return client.get_all("roleManagement/directory/roleAssignments?$select=id,principalId,roleDefinitionId,directoryScopeId")
    except Exception:
        return client.get_all("roleManagement/directory/roleAssignments")

def _get_pim_active_instances(client) -> List[Dict[str, Any]]:
    try:
        return client.get_all("roleManagement/directory/roleAssignmentScheduleInstances?$select=id,principalId,roleDefinitionId,startDateTime,endDateTime,directoryScopeId")
    except Exception:
        return client.get_all("roleManagement/directory/roleAssignmentScheduleInstances")

def _get_pim_eligible_instances(client) -> List[Dict[str, Any]]:
    try:
        return client.get_all("roleManagement/directory/roleEligibilityScheduleInstances?$select=id,principalId,roleDefinitionId,startDateTime,endDateTime,directoryScopeId")
    except Exception:
        return client.get_all("roleManagement/directory/roleEligibilityScheduleInstances")

def _batch_get_users(client, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out = {}
    ids = [i for i in list(dict.fromkeys(ids or [])) if i]
    for chunk in _chunk(ids, OR_LIMIT):
        flt = " or ".join([f"id eq '{i}'" for i in chunk])
        try:
            rows = client.get_all("users?$select=id,displayName,userPrincipalName,userType,accountEnabled" f"&$filter={flt}")
            for r in rows or []:
                out[r["id"]] = r
        except Exception as ex:
            fncPrintMessage(f"[PE] user chunk failed: {ex}", "warn")
    return out

def _batch_get_groups(client, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out = {}
    ids = [i for i in list(dict.fromkeys(ids or [])) if i]
    for chunk in _chunk(ids, OR_LIMIT):
        flt = " or ".join([f"id eq '{i}'" for i in chunk])
        try:
            rows = client.get_all("groups?$select=id,displayName,isAssignableToRole,securityEnabled,groupTypes,visibility" f"&$filter={flt}")
            for r in rows or []:
                out[r["id"]] = r
        except Exception as ex:
            fncPrintMessage(f"[PE] group chunk failed: {ex}", "warn")
    return out

def _batch_get_sps(client, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out = {}
    ids = [i for i in list(dict.fromkeys(ids or [])) if i]
    for chunk in _chunk(ids, OR_LIMIT):
        flt = " or ".join([f"id eq '{i}'" for i in chunk])
        try:
            rows = client.get_all("servicePrincipals?$select=id,displayName,servicePrincipalType,publisherName,appId,accountEnabled" f"&$filter={flt}")
            for r in rows or []:
                out[r["id"]] = r
        except Exception as ex:
            fncPrintMessage(f"[PE] sp chunk failed: {ex}", "warn")
    return out

def _resolve_principals(client, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    ids = list(dict.fromkeys([i for i in ids or [] if i]))
    users = _batch_get_users(client, ids)
    left = [i for i in ids if i not in users]
    groups = _batch_get_groups(client, left)
    left = [i for i in left if i not in groups]
    sps = _batch_get_sps(client, left)

    out = {}
    for k, v in users.items():
        out[k] = {"type": "User", "name": v.get("displayName") or v.get("userPrincipalName") or k, "raw": v}
    for k, v in groups.items():
        out[k] = {"type": "Group", "name": v.get("displayName") or k, "raw": v}
    for k, v in sps.items():
        out[k] = {"type": "ServicePrincipal", "name": v.get("displayName") or k, "raw": v}
    for k in ids:
        out.setdefault(k, {"type": "Unknown", "name": k, "raw": {"id": k}})
    return out

def _get_group_owners(client, gid: str) -> List[Dict[str, Any]]:
    try:
        return client.get_all(f"groups/{gid}/owners?$select=id,displayName,userPrincipalName")
    except Exception as ex:
        fncPrintMessage(f"[PE] owners for group {gid} failed: {ex}", "warn")
        return []

def _get_group_members_ids(client, gid: str) -> List[str]:
    ids: List[str] = []
    try:
        rows = client.get_all(f"groups/{gid}/members?$select=id")
        for r in rows or []:
            if isinstance(r, dict) and r.get("id"):
                ids.append(r["id"])
    except Exception as ex:
        fncPrintMessage(f"[PE] members for group {gid} failed: {ex}", "warn")
    return ids

# Risk scoring (simple heuristic)
CRITICAL_ROLES = {
    "Global Administrator",
    "Privileged Role Administrator",
    "User Administrator",
    "Application Administrator",
    "Cloud Application Administrator",
    "Security Administrator",
}

def _score(role_name: str, principal_type: str, flavor: str) -> int:
    base = 10 if role_name in CRITICAL_ROLES else 5
    if principal_type == "Group": base += 4
    if flavor == "Eligible":      base -= 3
    return max(1, min(100, base * 6))

# ---------------- main ----------------

def run(client, args):
    run_id = fncNewRunId("priv-esc")
    ts = _iso_now()
    fncPrintMessage(f"Running Privilege Escalation Pathing (run={run_id})", "info")

    role_defs = _get_role_definitions(client)

    permanent = _get_permanent_role_assignments(client) or []
    active    = _get_pim_active_instances(client) or []
    eligible  = _get_pim_eligible_instances(client) or []

    # Resolve principals
    principal_ids = [r.get("principalId") for r in (permanent + active + eligible) if r.get("principalId")]
    prin_map = _resolve_principals(client, principal_ids)

    findings: List[Dict[str, Any]] = []

    def add_finding(user_or_obj: str, path: str, details: str, role: str, principal_type: str, flavor: str):
        findings.append({
            "User": user_or_obj,
            "EscalationPath": path,
            "Role": role,
            "PrincipalType": principal_type,
            "Flavor": flavor,  # Permanent / Active / Eligible / GroupOwner / SelfGA
            "Details": details,
            "RiskScore": _score(role, principal_type, flavor),
        })

    # 1) Permanent admin assignments
    for r in permanent:
        pid = r.get("principalId"); rid = r.get("roleDefinitionId")
        rname = role_defs.get(rid, {}).get("displayName", rid)
        pm = prin_map.get(pid, {"type":"Unknown","name":pid})
        add_finding(pm["name"], "Permanent role assignment",
                    f"Principal is permanently assigned '{rname}'",
                    rname, pm["type"], "Permanent")

    # 2) PIM Active
    for r in active:
        pid = r.get("principalId"); rid = r.get("roleDefinitionId")
        rname = role_defs.get(rid, {}).get("displayName", rid)
        pm = prin_map.get(pid, {"type":"Unknown","name":pid})
        add_finding(pm["name"], "PIM Active (elevated now)",
                    f"Active window {r.get('startDateTime')}-{r.get('endDateTime')}",
                    rname, pm["type"], "Active")

    # 3) PIM Eligible
    for r in eligible:
        pid = r.get("principalId"); rid = r.get("roleDefinitionId")
        rname = role_defs.get(rid, {}).get("displayName", rid)
        pm = prin_map.get(pid, {"type":"Unknown","name":pid})
        add_finding(pm["name"], "PIM Eligible (on-demand elevation)",
                    f"Eligible window {r.get('startDateTime')}-{r.get('endDateTime')}",
                    rname, pm["type"], "Eligible")

    # 4) Group-based escalation — privileged groups
    group_assignees = [r for r in permanent if prin_map.get(r.get("principalId"),{}).get("type")=="Group"]
    roles_by_group: Dict[str, List[str]] = {}
    for r in group_assignees:
        gid = r.get("principalId"); rid = r.get("roleDefinitionId")
        roles_by_group.setdefault(gid, []).append(role_defs.get(rid, {}).get("displayName", rid))

    for gid, role_names in roles_by_group.items():
        gmeta = prin_map.get(gid, {"name": gid, "raw": {}})
        owners = _get_group_owners(client, gid)
        for o in owners:
            oname = o.get("displayName") or o.get("userPrincipalName") or o.get("id")
            add_finding(oname, "Group owner of privileged group",
                        f"Owner of role-assignable group '{gmeta.get('name')}', roles: {', '.join(role_names)}",
                        ", ".join(role_names) if role_names else "(unknown role)",
                        "User", "GroupOwner")

    # 5) Self-assign GA candidates (via PRA/GA)
    SELF_GRANT_ROLES = {"Privileged Role Administrator", "Global Administrator"}
    def _role_name(rid): return role_defs.get(rid, {}).get("displayName", rid)

    ga_self_candidates: List[Dict[str, Any]] = []

    def _add_self_ga_row(name: str, pid: str, why: str, flavor: str):
        ga_self_candidates.append({
            "User": name,
            "Why":  why,
            "Flavor": flavor,
            "Details": why,
            "RiskScore": 95 if flavor in ("Permanent","Active") else 80,
        })

    for r in (permanent + active + eligible):
        pid = r.get("principalId"); rid = r.get("roleDefinitionId")
        rname = _role_name(rid)
        if rname in SELF_GRANT_ROLES:
            pm = prin_map.get(pid, {"type":"Unknown","name":pid})
            flav = "Permanent"
            if r in active: flav = "Active"
            elif r in eligible: flav = "Eligible"
            _add_self_ga_row(pm["name"], pid, f"Holds '{rname}' ({flav}) → can assign GA to self", flav)

    # 6) Owners ALSO Members (Privileged Groups)
    owner_membership_issues: List[Dict[str, Any]] = []
    for gid, role_names in roles_by_group.items():
        gmeta = prin_map.get(gid, {"name": gid})
        owners = _get_group_owners(client, gid) or []
        member_ids = set(_get_group_members_ids(client, gid))
        for o in owners:
            oid = o.get("id")
            if not oid: continue
            if oid in member_ids:
                oname = o.get("displayName") or o.get("userPrincipalName") or oid
                owner_membership_issues.append({
                    "GroupName": gmeta.get("name", gid),
                    "GroupId": gid,
                    "OwnerName": oname,
                    "OwnerId": oid,
                    "PrivilegedRoles": ", ".join(role_names) if role_names else "(unknown role)",
                    "Issue": "Owner is also a member of the privileged group",
                    "Details": f"{oname} is owner AND member of '{gmeta.get('name', gid)}' (roles: {', '.join(role_names)})",
                })

    # 7) Owner Self-Assign Opportunities (separate box)
    # For each privileged group: owners who are NOT members, and could add themselves.
    # SelfAssignEnabled = isAssignableToRole == True AND securityEnabled == True AND NOT dynamic group.
    owner_self_assign_ops: List[Dict[str, Any]] = []
    for gid, role_names in roles_by_group.items():
        gmeta = prin_map.get(gid, {"name": gid, "raw": {}})
        rawg = gmeta.get("raw", {})
        is_assignable = bool(rawg.get("isAssignableToRole"))
        is_sec = bool(rawg.get("securityEnabled", True))
        gtypes = [x.lower() for x in (rawg.get("groupTypes") or [])]
        is_dynamic = any("dynamic" in x for x in gtypes)
        reason_bits = []
        if not is_assignable: reason_bits.append("Group not role-assignable")
        if not is_sec: reason_bits.append("Group not securityEnabled")
        if is_dynamic: reason_bits.append("Dynamic membership (owner cannot add self)")

        member_ids = set(_get_group_members_ids(client, gid))
        for o in _get_group_owners(client, gid) or []:
            oid = o.get("id")
            if not oid: continue
            if oid in member_ids:
                # already in the other section
                continue
            oname = o.get("displayName") or o.get("userPrincipalName") or oid
            self_ok = is_assignable and is_sec and not is_dynamic
            owner_self_assign_ops.append({
                "GroupName": gmeta.get("name", gid),
                "GroupId": gid,
                "OwnerName": oname,
                "OwnerId": oid,
                "PrivilegedRoles": ", ".join(role_names) if role_names else "(unknown role)",
                "SelfAssignEnabled": "Yes" if self_ok else "No",
                "Reason": ("; ".join(reason_bits) if not self_ok else "Meets basic conditions (manual add allowed)"),
                "Details": f"{oname} can add self to '{gmeta.get('name', gid)}' to gain: {', '.join(role_names)}" if self_ok
                           else f"Blocked: {', '.join(reason_bits) or 'unknown'}",
            })

    # ---------- Console Previews ----------
    find_headers = ["User","EscalationPath","Role","PrincipalType","Flavor","Details","RiskScore"]
    ga_headers   = ["User","Why","Flavor","Details","RiskScore"]
    om_headers   = ["GroupName","GroupId","OwnerName","OwnerId","PrivilegedRoles","Issue","Details"]
    os_headers   = ["GroupName","GroupId","OwnerName","OwnerId","PrivilegedRoles","SelfAssignEnabled","Reason","Details"]

    safe_findings = _sanitize_table(findings, find_headers)
    safe_ga       = _sanitize_table(ga_self_candidates, ga_headers)
    safe_om       = _sanitize_table(owner_membership_issues, om_headers)
    safe_os       = _sanitize_table(owner_self_assign_ops, os_headers)

    fncPrintMessage("Privilege Escalation Findings (top 50)", "info")
    if safe_findings:
        print(fncToTable(safe_findings[:50], headers=find_headers, max_rows=min(50, len(safe_findings))))
    else:
        print("(no findings)")

    if safe_ga:
        fncPrintMessage("GA Self-Assign Path Candidates", "info")
        print(fncToTable(safe_ga[:25], headers=ga_headers, max_rows=min(25, len(safe_ga))))

    if safe_om:
        fncPrintMessage("Owners also Members (Privileged Groups)", "info")
        print(fncToTable(safe_om[:25], headers=om_headers, max_rows=min(25, len(safe_om))))

    if safe_os:
        fncPrintMessage("Owner Self-Assign Opportunities", "info")
        print(fncToTable(safe_os[:25], headers=os_headers, max_rows=min(25, len(safe_os))))

    # ---------- KPIs / Charts ----------
    total = len(safe_findings)
    kpis = [
        {"label":"Total Findings","value":str(total),"tone":"primary","icon":"bi-search"},
        {"label":"Permanent Admin","value":str(sum(1 for r in safe_findings if r["Flavor"]=="Permanent")),"tone":"danger","icon":"bi-shield-lock"},
        {"label":"PIM Active","value":str(sum(1 for r in safe_findings if r["Flavor"]=="Active")),"tone":"warning","icon":"bi-lightning-charge"},
        {"label":"PIM Eligible","value":str(sum(1 for r in safe_findings if r["Flavor"]=="Eligible")),"tone":"secondary","icon":"bi-hourglass-split"},
        {"label":"Group Owner Paths","value":str(sum(1 for r in safe_findings if r["Flavor"]=="GroupOwner")),"tone":"info","icon":"bi-people"},
        {"label":"GA Self-Path (unique)","value":str(len(safe_ga)),"tone":"danger","icon":"bi-exclamation-octagon"},
        {"label":"Owners Also Members","value":str(len(safe_om)),"tone":"warning","icon":"bi-people-fill"},
        {"label":"Owner Self-Assign Ops","value":str(len(safe_os)),"tone":"secondary","icon":"bi-person-plus"},
    ]

    bucket_counts = {"Permanent":0,"Active":0,"Eligible":0,"GroupOwner":0}
    for r in safe_findings:
        b = r.get("Flavor","")
        if b in bucket_counts: bucket_counts[b] += 1
    chart_labels = ["Permanent","Active","Eligible","GroupOwner"]
    chart_values = [bucket_counts["Permanent"], bucket_counts["Active"], bucket_counts["Eligible"], bucket_counts["GroupOwner"]]

    data = {
        "provider": "entra",
        "run_id": run_id,
        "timestamp": ts,
        "summary": {
            "Total Findings": total,
            "GA Self-Path Candidates": len(safe_ga),
            "Owners Also Members (Priv Groups)": len(safe_om),
            "Owner Self-Assign Ops": len(safe_os),
        },
        "findings": safe_findings,
        "ga_self_path_candidates": safe_ga,
        "owner_membership_issues": safe_om,
        "owner_self_assign_ops": safe_os,

        "_kpis": kpis,
        "_charts": {
            "place": "summary",
            "byFlavor": {"labels": chart_labels, "data": chart_values},
        },

        "_title": "Privilege Escalation Paths",
        "_subtitle": "Potential routes to higher privileges (incl. self-assign GA & owner self-assign ops)",
        "_container_class": "priv-esc",
        "_inline_css": PE_CSS,
        "_inline_js":  PE_JS,
    }

    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "priv_esc_pathing", data)

    fncPrintMessage("Privilege Escalation Pathing module complete.", "success")
    return data
