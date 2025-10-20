# ================================================================
# File     : modules/entra/group_audit.py
# Purpose  : Enumerate Entra groups with a safe baseline:
#            - core properties & counts
#            - app role assignments count
#            - directory role assignments count
#            - simple impact/likelihood/risk
#            - built-in role summary with warnings
# Output   : data["groups_top"]  (overview; slim columns)
#            data["group_details"] (per-group details + Counts)
#            data["role_assignments_overview"] (built-in roles)
#            NEW: _kpis, _standouts, _charts for dashboard boxes
# Notes    : Uses reporting.py dashboard (cards + charts at top),
#            retains scoped CSS/JS for table drawers/search.
# ================================================================

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

from core.utils import (
    fncPrintMessage,
    fncNewRunId,
    fncToTable,
)
from core.reporting import fncWriteHTMLReport


REQUIRED_PERMS = [
    "Group.Read.All",
    "Directory.Read.All",
    "AppRoleAssignment.Read.All",
    "RoleManagement.Read.Directory",
]

# ----------------------- module-local CSS ------------------------
GROUP_AUDIT_CSS = r"""
.group-audit .ga-clickable { cursor: pointer; }
.group-audit .ga-clickable:hover { background: rgba(255,255,255,.05); }
.group-audit .ga-expander > td { padding: 0; background: #10141b; }
.group-audit .ga-expander-body { padding: 14px 16px; border-top: 1px solid rgba(255,255,255,.08); }

/* Overview table: keep columns tight and only show selected ones */
.group-audit table[data-key="groups_top"] th,
.group-audit table[data-key="groups_top"] td { white-space: nowrap; }
.group-audit .ga-hide { display: none !important; }

/* Prevent Details JSON from expanding table / wrap long tokens */
.group-audit .cp-json,
.group-audit .cp-json pre,
.group-audit .cp-json code {
  white-space: pre-wrap !important;
  word-break: break-word !important;
  overflow-x: hidden !important;
}
.group-audit table[data-key="group_details"]{
  table-layout: fixed; /* keep grid compact even with long JSON */
}
.group-audit table[data-key="group_details"] th,
.group-audit table[data-key="group_details"] td{
  word-break: break-word;
}
.group-audit table[data-key="group_details"] .cp-json > summary{
  max-width: 520px; /* cap JSON summary width */
}

/* Chevron in DisplayName cell */
.group-audit .ga-name { display:inline-flex; align-items:center; gap:8px; }
.group-audit .ga-chevron { display:inline-block; width:1em; transition: transform .15s ease; opacity:.85; }
.group-audit .ga-open .ga-chevron { transform: rotate(90deg); }

/* Drawer layout */
.group-audit .ga-flex { display: grid; grid-template-columns: 1fr 1.2fr; gap: 16px; }
@media (max-width: 1100px){ .group-audit .ga-flex { grid-template-columns: 1fr; } }
.group-audit .ga-pane {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: color-mix(in srgb, var(--card) 94%, #000 6%);
  overflow: hidden;
}
.group-audit .ga-pane h5 {
  margin: 0; padding: 10px 12px;
  background: color-mix(in srgb, var(--accent2) 85%, #000 15%);
  color: #fff; font-weight: 700; border-bottom: 1px solid rgba(0,0,0,.2);
}
.group-audit .ga-kv { width: 100%; border-collapse: collapse; }
.group-audit .ga-kv th, .group-audit .ga-kv td {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top;
}
.group-audit .ga-kv th {
  width: 210px; white-space: nowrap;
  background: color-mix(in srgb, var(--accent2) 12%, var(--card)); font-weight: 600;
}
.group-audit .ga-kv tr:last-child td, .group-audit .ga-kv tr:last-child th { border-bottom: 0; }

/* Monospaced look for the raw details source table */
.group-audit table[data-key="group_details"] td {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
}

/* Toolbar (search + view more) */
.group-audit .ga-toolbar{
  display:flex; gap:10px; align-items:center; margin:6px 2px 0 2px; flex-wrap:wrap;
}
.group-audit .ga-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:220px; outline:none;
}
.group-audit .ga-toolbar .btn{
  padding:6px 12px; border:1px solid var(--border); border-radius:999px;
  background:var(--card); cursor:pointer; font-weight:600;
}
.group-audit .ga-toolbar .btn.primary{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}
"""

# ----------------------- module-local JS -------------------------
GROUP_AUDIT_JS = r"""
(function () {
  const root = document.querySelector('.group-audit') || document;

  // Known table ids from reporting.py
  const top = root.querySelector('#tbl-groups-top');
  const det = root.querySelector('#tbl-group-details');
  if (top) top.setAttribute('data-key','groups_top');
  if (det) det.setAttribute('data-key','group_details');
  if (!top) return;

  // ---- helpers ----
  const txt = el => (el?.textContent || '').trim();
  const slug = s => (s||'').toLowerCase().replace(/[^a-z0-9]+/g,'-');

  function hideColumns(table, keepSet) {
    // Columns that must NEVER be visible in grid
    const HIDE_ALWAYS = new Set(['Id', 'Details']);

    const ths = Array.from(table.querySelectorAll('thead th'));
    const idxByName = new Map();
    ths.forEach((th,i)=> idxByName.set((th.textContent || '').trim(), i));

    ths.forEach((th,i)=>{
      const name = (th.textContent || '').trim();
      if (HIDE_ALWAYS.has(name) || !keepSet.has(name)) {
        table
          .querySelectorAll(`thead th:nth-child(${i+1}), tbody td:nth-child(${i+1})`)
          .forEach(c => c.classList.add('ga-hide'));
      }
    });

    return idxByName;
  }

  function buildDetailsMap(table){
    const map = new Map();
    if (!table) return map;
    const headers = Array.from(table.querySelectorAll('thead th')).map(h=>txt(h));
    Array.from(table.querySelectorAll('tbody tr')).forEach(tr=>{
      const tds = Array.from(tr.children);
      if (!tds.length) return;
      const gid = txt(tds[0]);
      const m = {};
      headers.forEach((name, idx) => m[name] = tds[idx] ? tds[idx].innerHTML : '');
      map.set(gid, m);
    });
    return map;
  }

  function attachRowDrawer(table, options){
    const { idIdx, nameIdx, detailsById, isDetailsTable } = options;
    const headCells = Array.from(table.querySelectorAll('thead th'));
    const totalCols = headCells.length;

    Array.from(table.querySelectorAll('tbody tr')).forEach(tr=>{
      const idCell   = idIdx >= 0 ? tr.children[idIdx] : null;
      const nameCell = nameIdx >= 0 ? tr.children[nameIdx] : tr.children[0];
      if (!nameCell) return;

      const label = txt(nameCell);
      nameCell.innerHTML = `<span class="ga-name"><span class="ga-chevron">▸</span><span class="ga-label"></span></span>`;
      nameCell.querySelector('.ga-label').textContent = label;

      tr.classList.add('ga-clickable');
      tr.addEventListener('click', ()=>{
        const gid  = idCell ? txt(idCell) : (isDetailsTable ? txt(tr.children[0]) : '');
        const open = tr.classList.contains('ga-open');
        const next = tr.nextElementSibling;
        if (next && next.classList.contains('ga-expander')) next.remove();
        tr.classList.remove('ga-open');
        if (open) return;

        let src = {};
        if (isDetailsTable) {
          const headers = Array.from(table.querySelectorAll('thead th')).map(h=>txt(h));
          const tds = Array.from(tr.children);
          headers.forEach((name, idx) => src[name] = tds[idx] ? tds[idx].innerHTML : '');
        } else {
          src = detailsById.get(gid) || {};
        }

        const type    = src['Type'] || '';
        const nUsers  = src['Number of Users'] || '';
        const nSpns   = src['SPNs'] || '';
        const nNested = src['Number of Nested Groups'] || '';
        const details = src['Details'] || '';

        const html = `
          <div class="ga-expander-body">
            <div class="ga-flex">
              <div class="ga-pane">
                <h5>Overview</h5>
                <table class="ga-kv"><tbody>
                  <tr><th>Display Name</th><td>${label}</td></tr>
                  <tr><th>Id</th><td>${gid || '<em>unknown</em>'}</td></tr>
                  <tr><th>Type</th><td>${type || '<em>unknown</em>'}</td></tr>
                  <tr><th>Number of Users</th><td>${nUsers || 0}</td></tr>
                  <tr><th>SPNs</th><td>${nSpns || 0}</td></tr>
                  <tr><th>Number of Nested Groups</th><td>${nNested || 0}</td></tr>
                </tbody></table>
              </div>
              <div class="ga-pane">
                <h5>Details</h5>
                <table class="ga-kv"><tbody>
                  <tr><th>Full Object</th><td>${details || '<em>none available</em>'}</td></tr>
                </tbody></table>
              </div>
            </div>
          </div>`;

        const exp = document.createElement('tr');
        const td  = document.createElement('td');
        exp.className = 'ga-expander';
        td.colSpan = totalCols;
        td.innerHTML = html;
        exp.appendChild(td);
        tr.parentNode.insertBefore(exp, tr.nextSibling);
        tr.classList.add('ga-open');

        const chev = tr.querySelector('.ga-chevron');
        if (chev) chev.textContent = '▾';
      });
    });
  }

  function addToolbar(table, title){
    const card = table.closest('.card');
    if (!card) return;
    const bar = document.createElement('div');
    bar.className = 'ga-toolbar';
    const tableSlug = slug(title);
    bar.innerHTML = `
      <input type="search" placeholder="Search ${title}…" aria-label="Search ${title}" data-for="${tableSlug}">
      <button class="btn primary" data-action="viewmore" data-for="${tableSlug}" style="display:none">View more…</button>
    `;
    card.insertBefore(bar, card.querySelector('.tablewrap'));
    return bar;
  }

  function paginateAndSearch(table, toolbar){
    const search = toolbar.querySelector('input[type="search"]');
    const viewMoreBtn = toolbar.querySelector('[data-action="viewmore"]');

    const rows = Array.from(table.querySelectorAll('tbody tr'));
    const PAGE = 20;
    let expanded = false;

    function apply(){
      const q = (search.value||'').toLowerCase();
      let shown = 0;
      rows.forEach((tr, idx)=>{
        const match = !q || tr.textContent.toLowerCase().includes(q);
        if (!match) { tr.style.display = 'none'; return; }
        if (!expanded && q === '' && shown >= PAGE) { tr.style.display = 'none'; return; }
        tr.style.display = '';
        shown++;
      });
      const moreExists = rows.filter(tr => tr.style.display !== 'none').length < rows.length && q === '' && !expanded;
      viewMoreBtn.style.display = moreExists ? '' : 'none';
    }

    apply();
    search.addEventListener('input', apply);
    viewMoreBtn.addEventListener('click', ()=>{ expanded = true; apply(); });
  }

  // ------------ Overview table (top) ------------
  const keepTop = new Set(['Id','DisplayName','Type','Visibility','Risk']);
  const idxTop  = hideColumns(top, keepTop);
  const detMap  = buildDetailsMap(det);
  attachRowDrawer(top, {
    idIdx: idxTop.has('Id') ? idxTop.get('Id') : -1,
    nameIdx: idxTop.has('DisplayName') ? idxTop.get('DisplayName') : -1,
    detailsById: detMap,
    isDetailsTable: false
  });
  const barTop = addToolbar(top, 'Groups Top');
  if (barTop) paginateAndSearch(top, barTop);

  // ------------ Details table (det) ------------
  if (det) {
    const headers = Array.from(det.querySelectorAll('thead th')).map(h=>txt(h));
    const idIdx = headers.indexOf('Id');
    const nameIdx = headers.indexOf('Display Name');

    /* Hide Id and Details columns in the grid to keep it compact;
       the data stays in the DOM for the drawer. */
    headers.forEach((name, idx)=>{
      if (name === 'Id' || name === 'Details') {
        det.querySelectorAll(`thead th:nth-child(${idx+1}), tbody td:nth-child(${idx+1})`)
          .forEach(c => c.classList.add('ga-hide'));
      }
    });

    attachRowDrawer(det, {
      idIdx: idIdx >= 0 ? idIdx : 0,
      nameIdx: nameIdx >= 0 ? nameIdx : 1,
      detailsById: null,
      isDetailsTable: true
    });
    const barDet = addToolbar(det, 'Group Details');
    if (barDet) paginateAndSearch(det, barDet);
  }
})();
"""

# ----------------------- helpers & scoring -----------------------

ROLE_WARN_THRESHOLDS: Dict[str, int] = {
    "Global Administrator": 5,
    "Privileged Role Administrator": 10,
    "User Administrator": 10,
}

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_get(obj: Dict, *path, default=None):
    cur = obj
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def _group_type(g: Dict[str, Any]) -> str:
    gtypes = g.get("groupTypes") or []
    if "Unified" in gtypes:
        return "M365 Group"
    if g.get("securityEnabled"):
        return "Security Group"
    if g.get("mailEnabled"):
        return "Distribution Group"
    return "Group"

def _bucket_from_risk(risk: int) -> str:
    if risk is None:
        return "unknown"
    if risk >= 80:
        return "critical"
    if risk >= 40:
        return "warning"
    return "ok"

def _impact_likelihood_row(g: Dict[str, Any], counts: Dict[str, int]) -> Dict[str, int]:
    impact = 0
    if g.get("securityEnabled"): impact += 2
    if g.get("isAssignableToRole"): impact += 10
    if counts.get("appRoles"): impact += 3
    if counts.get("roleAssignments"): impact += 6

    likelihood = 0
    if counts.get("users", 0) > 0:  likelihood += 2
    if counts.get("guests", 0) > 0: likelihood += 2
    if counts.get("groups", 0) > 0: likelihood += 2
    if counts.get("sps", 0) > 0:    likelihood += 2

    risk = int(impact * max(1, likelihood))
    return {"impact": impact, "likelihood": likelihood, "risk": risk}

# --------------------- Graph helpers (v1.0-safe) ---------------------

def _get_groups(client) -> List[Dict[str, Any]]:
    url = (
        "groups?"
        "$select=id,displayName,visibility,groupTypes,securityEnabled,"
        "isAssignableToRole,onPremisesSyncEnabled,mailEnabled,description,membershipRule"
    )
    return client.get_all(url)

def _get_ids(urls: List[str], client) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for u in urls:
        parts = u.split("/")
        group_id = parts[1] if len(parts) > 1 else ""
        try:
            rows = client.get_all(u + "?$select=id")
            if any("@odata.type" not in r for r in rows):
                rows = client.get_all(u)
            out[group_id] = rows
        except Exception as ex:
            fncPrintMessage(f"Failed to list {u}: {ex}", "warn")
            out[group_id] = []
    return out

def _batch_get_users(client, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return out
    for chunk in [ids[i:i+20] for i in range(0, len(ids), 20)]:
        flt = " or ".join([f"id eq '{i}'" for i in chunk])
        rows = client.get_all(
            "users?$select=id,userType,accountEnabled,onPremisesSyncEnabled,userPrincipalName"
            f"&$filter={flt}"
        )
        for r in rows:
            out[r["id"]] = r
    return out

def _batch_get_sps(client, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return out
    for chunk in [ids[i:i+20] for i in range(0, len(ids), 20)]:
        flt = " or ".join([f"id eq '{i}'" for i in chunk])
        rows = client.get_all(
            "servicePrincipals?"
            "$select=id,displayName,servicePrincipalType,publisherName,appOwnerOrganizationId,accountEnabled"
            f"&$filter={flt}"
        )
        for r in rows:
            out[r["id"]] = r
    return out

def _get_grouprole_assignments(client, group_id: str) -> List[Dict[str, Any]]:
    url = (
        "roleManagement/directory/roleAssignments?"
        f"$filter=principalId eq '{group_id}'"
        "&$expand=roleDefinition($select=displayName)"
    )
    try:
        return client.get_all(url)
    except Exception as ex:
        fncPrintMessage(f"Role assignments fetch failed (group {group_id}): {ex}", "warn")
        return []

def _get_group_approles(client, group_id: str) -> List[Dict[str, Any]]:
    try:
        return client.get_all(
            f"groups/{group_id}/appRoleAssignments?$select=resourceDisplayName,resourceId,appRoleId"
        )
    except Exception as ex:
        fncPrintMessage(f"App role assignments fetch failed (group {group_id}): {ex}", "warn")
        return []

# -------- Directory roles (built-in) summary helpers --------

def _get_all_directory_role_assignments(client) -> List[Dict[str, Any]]:
    url = (
        "roleManagement/directory/roleAssignments?"
        "$select=id,principalId,roleDefinitionId,directoryScopeId"
        "&$expand=roleDefinition($select=displayName,isBuiltIn)"
    )
    return client.get_all(url)

def _summarise_built_in_roles(assignments: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    by_def: Dict[str, Dict[str, Any]] = {}
    for a in assignments:
        rd = a.get("roleDefinition") or {}
        name = rd.get("displayName") or "(unknown role)"
        is_built_in = bool(rd.get("isBuiltIn"))
        if not is_built_in:
            continue
        key = a.get("roleDefinitionId") or name
        rec = by_def.setdefault(key, {
            "roleName": name,
            "isBuiltIn": is_built_in,
            "assigneeIds": set(),
        })
        pid = a.get("principalId")
        if pid:
            rec["assigneeIds"].add(pid)

    warnings: List[str] = []
    rows: List[Dict[str, Any]] = []
    for rec in by_def.values():
        count = len(rec["assigneeIds"])
        name = rec["roleName"]
        threshold = ROLE_WARN_THRESHOLDS.get(name)
        warn_txt = ""
        if threshold is not None and count > threshold:
            warn_txt = f"High number of assignees for '{name}' ({count} > {threshold})"
            warnings.append(warn_txt)
        rows.append({
            "Role": name,
            "BuiltIn": rec["isBuiltIn"],
            "Assignees": count,
            "Threshold": threshold if threshold is not None else "-",
            "Warning": warn_txt or "-",
        })

    def sort_key(r):
        pri = 0 if r["Role"] == "Global Administrator" else 1
        return (pri, -int(r["Assignees"]))
    rows.sort(key=sort_key)
    return rows, warnings

# --------------------- Module entry point -----------------------

def run(client, args):
    run_id = fncNewRunId("groups")
    ts = datetime.now(timezone.utc).isoformat()
    fncPrintMessage("Starting module: entra/group_audit", "info")
    fncPrintMessage(f"Running Group Audit (run={run_id})", "info")

    groups = _get_groups(client)

    # Fetch members/owners (IDs + @odata.type)
    member_urls = [f"groups/{g['id']}/members" for g in groups]
    owner_urls  = [f"groups/{g['id']}/owners"  for g in groups]
    members_map = _get_ids(member_urls, client)
    owners_map  = _get_ids(owner_urls, client)

    # Collect users/SP ids
    all_member_user_ids, all_owner_user_ids = [], []
    all_member_sp_ids,   all_owner_sp_ids   = [], []

    for g in groups:
        gid = g["id"]
        for o in members_map.get(gid, []):
            t = (o.get("@odata.type") or "").lower()
            if t.endswith("user"):
                all_member_user_ids.append(o["id"])
            elif t.endswith("serviceprincipal"):
                all_member_sp_ids.append(o["id"])
        for o in owners_map.get(gid, []):
            t = (o.get("@odata.type") or "").lower()
            if t.endswith("user"):
                all_owner_user_ids.append(o["id"])
            elif t.endswith("serviceprincipal"):
                all_owner_sp_ids.append(o["id"])

    # Dedup & hydrate
    all_member_user_ids = list(dict.fromkeys(all_member_user_ids))
    all_owner_user_ids  = list(dict.fromkeys(all_owner_user_ids))
    all_member_sp_ids   = list(dict.fromkeys(all_member_sp_ids))
    all_owner_sp_ids    = list(dict.fromkeys(all_owner_sp_ids))

    users_by_id = _batch_get_users(client, list(set(all_member_user_ids + all_owner_user_ids)))
    sps_by_id   = _batch_get_sps(client,   list(set(all_member_sp_ids   + all_owner_sp_ids)))

    overview_rows: List[Dict[str, Any]] = []
    detail_rows:   List[Dict[str, Any]] = []

    # Aggregates for dashboard
    bucket_counts = {"critical":0, "warning":0, "ok":0, "unknown":0}
    with_roles = 0
    with_approles = 0
    with_guests = 0
    dynamic_groups = 0

    name_by_id = {g["id"]: g.get("displayName", "") for g in groups}
    top_risky = None
    top_roles = None
    top_approles = None

    for g in groups:
        gid = g["id"]
        mem = members_map.get(gid, [])
        own = owners_map.get(gid,  [])

        mem_users  = [m for m in mem if (m.get("@odata.type","").lower().endswith("user"))]
        mem_groups = [m for m in mem if (m.get("@odata.type","").lower().endswith("group"))]
        mem_sps    = [m for m in mem if (m.get("@odata.type","").lower().endswith("serviceprincipal"))]

        own_users = [o for o in own if (o.get("@odata.type","").lower().endswith("user"))]
        own_sps   = [o for o in own if (o.get("@odata.type","").lower().endswith("serviceprincipal"))]

        approles = _get_group_approles(client, gid)
        roles    = _get_grouprole_assignments(client, gid)

        role_names = sorted(
            { _safe_get(r, "roleDefinition", "displayName") for r in roles if _safe_get(r, "roleDefinition", "displayName") }
        )
        role_assignable_display = ", ".join(role_names) if role_names else "No Role Assignable"

        n_users   = len(mem_users)
        n_groups  = len(mem_groups)
        n_spns    = len(mem_sps)
        n_guests  = sum(1 for u in mem_users if (users_by_id.get(u["id"], {}).get("userType") == "Guest"))

        counts = {
            "users": n_users,
            "guests": n_guests,
            "sps": n_spns,
            "groups": n_groups,
            "appRoles": len(approles),
            "roleAssignments": len(roles),
        }

        score = _impact_likelihood_row(g, counts)
        bucket = _bucket_from_risk(score["risk"])
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        if counts["roleAssignments"] > 0: with_roles += 1
        if counts["appRoles"] > 0: with_approles += 1
        if n_guests > 0: with_guests += 1
        if "DynamicMembership" in (g.get("groupTypes") or []): dynamic_groups += 1

        # track standouts
        if (top_risky is None) or (score["risk"] > top_risky["risk"]):
            top_risky = {"name": g.get("displayName","(unnamed)"), "risk": score["risk"], "bucket": bucket}
        if (top_roles is None) or (counts["roleAssignments"] > top_roles["count"]):
            top_roles = {"name": g.get("displayName","(unnamed)"), "count": counts["roleAssignments"]}
        if (top_approles is None) or (counts["appRoles"] > top_approles["count"]):
            top_approles = {"name": g.get("displayName","(unnamed)"), "count": counts["appRoles"]}

        # ---- Overview (slim) ----
        overview_rows.append({
            "Id": gid,
            "DisplayName": g.get("displayName",""),
            "Type": _group_type(g),
            "Visibility": g.get("visibility") or "Private",
            "Risk": score["risk"],
        })

        # ---- Details table with requested headers + a full Details object ----
        full_details = {
            "General": {
                "Type": _group_type(g),
                "Visibility": g.get("visibility") or "Private",
                "SecurityEnabled": bool(g.get("securityEnabled", False)),
                "RoleAssignable": role_assignable_display,
                "OnPrem": bool(g.get("onPremisesSyncEnabled", False)),
                "Dynamic": ("DynamicMembership" in (g.get("groupTypes") or [])),
                "Description": g.get("description",""),
                "MembershipRule": g.get("membershipRule",""),
            },
            "Owners (Users)": [
                {
                    "userPrincipalName": users_by_id.get(u["id"],{}).get("userPrincipalName",""),
                    "userType": users_by_id.get(u["id"],{}).get("userType",""),
                    "onPremisesSyncEnabled": users_by_id.get(u["id"],{}).get("onPremisesSyncEnabled", None),
                    "accountEnabled": users_by_id.get(u["id"],{}).get("accountEnabled", None),
                } for u in own_users
            ],
            "Owners (Service Principals)": [
                {
                    "displayName": sps_by_id.get(s["id"],{}).get("displayName",""),
                    "type": sps_by_id.get(s["id"],{}).get("servicePrincipalType",""),
                    "publisherName": sps_by_id.get(s["id"],{}).get("publisherName",""),
                    "accountEnabled": sps_by_id.get(s["id"],{}).get("accountEnabled", None),
                } for s in own_sps
            ],
            "Members (Users)": [
                {
                    "userPrincipalName": users_by_id.get(u["id"],{}).get("userPrincipalName",""),
                    "userType": users_by_id.get(u["id"],{}).get("userType",""),
                    "onPremisesSyncEnabled": users_by_id.get(u["id"],{}).get("onPremisesSyncEnabled", None),
                    "accountEnabled": users_by_id.get(u["id"],{}).get("accountEnabled", None),
                } for u in mem_users[:50]
            ],
            "Members (Groups)": [ {"id": x["id"]} for x in mem_groups[:50] ],
            "Members (Service Principals)": [
                {
                    "displayName": sps_by_id.get(s["id"],{}).get("displayName",""),
                    "type": sps_by_id.get(s["id"],{}).get("servicePrincipalType",""),
                    "publisherName": sps_by_id.get(s["id"],{}).get("publisherName",""),
                } for s in mem_sps[:50]
            ],
            "AppRoles": [
                {"resource": a.get("resourceDisplayName",""), "appRoleId": a.get("appRoleId","")}
                for a in approles
            ],
            "DirectoryRoleAssignments": [
                {
                    "displayName": _safe_get(r, "roleDefinition", "displayName"),
                    "directoryScopeId": r.get("directoryScopeId"),
                    "principalId": r.get("principalId"),
                } for r in roles
            ],
            "Score": {
                "impact": score["impact"],
                "likelihood": score["likelihood"],
                "risk": score["risk"],
                "bucket": bucket,
            },
            "Counts": counts,
        }

        detail_rows.append({
            "Id": gid,
            "Display Name": g.get("displayName",""),
            "Type": _group_type(g),
            "Number of Users": n_users,
            "SPNs": n_spns,
            "Number of Nested Groups": n_groups,
            "Details": full_details,
        })

    # Sort overview by risk desc
    overview_rows.sort(key=lambda r: r["Risk"], reverse=True)

    # Console table
    fncPrintMessage("[•] Groups Overview (sorted by risk)", "info")
    print(fncToTable(
        overview_rows,
        headers=["DisplayName","Type","Visibility","Risk"],
        max_rows=len(overview_rows),
    ))

    # Optional console nested view
    if groups:
        fncPrintMessage("[•] Direct nested groups (one level)", "info")
        for g in groups:
            gid = g["id"]
            mem_groups = [m for m in members_map.get(gid, []) if (m.get("@odata.type","").lower().endswith("group"))]
            if not mem_groups:
                continue
            print(name_by_id.get(gid, gid))
            for child in mem_groups:
                print(f" |-- {name_by_id.get(child['id'], child['id'])}")

    # Built-in role summary
    fncPrintMessage("[•] Summarising built-in Entra roles", "info")
    role_assignments = _get_all_directory_role_assignments(client)
    built_in_rows, role_warnings = _summarise_built_in_roles(role_assignments)
    if built_in_rows:
        print(fncToTable(
            built_in_rows,
            headers=["Role", "BuiltIn", "Assignees", "Threshold", "Warning"],
            max_rows=len(built_in_rows),
        ))
    for w in role_warnings:
        fncPrintMessage(w, "warn")

    # ---------- Dashboard content ----------
    total_groups = len(groups)
    kpis = [
        {"label":"Total Groups","value":str(total_groups),"tone":"primary","icon":"bi-people"},
        {"label":"With Dir Roles","value":str(with_roles),"tone":"danger","icon":"bi-shield-lock"},
        {"label":"With App Roles","value":str(with_approles),"tone":"warning","icon":"bi-plug"},
        {"label":"Groups w/ Guests","value":str(with_guests),"tone":"info","icon":"bi-person-exclamation"},
    ]
    if dynamic_groups:
        kpis.append({"label":"Dynamic Groups","value":str(dynamic_groups),"tone":"secondary","icon":"bi-arrow-repeat"})

    # Standouts (labels are free-form; mapped to tiles)
    standouts = {}
    if top_risky:
        risk_score = min(10.0, top_risky["risk"]/10.0)
        standouts["group"] = {
            "title":"Highest Risk Group",
            "name": top_risky["name"],
            "risk_score": float(risk_score),
            "comment": f"{top_risky['bucket'].title()} risk (score {top_risky['risk']})"
        }
    if top_roles:
        standouts["user"] = {
            "title":"Most Directory Roles",
            "name": top_roles["name"],
            "risk_score": float(min(10.0, top_roles["count"] or 0)),
            "comment": f"{top_roles['count']} role assignment(s)"
        }
    if top_approles:
        standouts["computer"] = {
            "title":"Most App Role Assignments",
            "name": top_approles["name"],
            "risk_score": float(min(10.0, top_approles["count"] or 0)),
            "comment": f"{top_approles['count']} app role assignment(s)"
        }

    severity_labels = ["Critical","Warning","OK","Unknown"]
    severity_values = [
        bucket_counts.get("critical",0),
        bucket_counts.get("warning",0),
        bucket_counts.get("ok",0),
        bucket_counts.get("unknown",0),
    ]

    data = {
        "provider": "entra",
        "run_id": run_id,
        "timestamp": ts,
        "summary": {
            "Total Groups": total_groups,
            "With Directory Roles": with_roles,
            "With App Roles": with_approles,
            "Groups w/ Guests": with_guests,
            "Dynamic Groups": dynamic_groups,
            "Role warnings": len(role_warnings),
        },
        "groups_top": overview_rows,
        "group_details": detail_rows,
        "role_assignments_overview": built_in_rows,

        # ===== NEW dashboard bits =====
        "_kpis": kpis,
        "_standouts": standouts,
        "_charts": {
            "place": "summary",  # render donut next to Summary panel
            "severity": {
                "labels": severity_labels,
                "data": severity_values,
            }
        },

        # ===== Keep scoped styling/behaviour for tables =====
        "_inline_css": GROUP_AUDIT_CSS,
        "_inline_js":  GROUP_AUDIT_JS,
        "_container_class": "group-audit",
        "_title": "Entra Group Audit",
        "_subtitle": "Overview, details, and built-in role exposure",
    }

    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "group_audit", data)

    fncPrintMessage("Group Overview module complete.", "success")
    return data
