# ================================================================
# File     : modules/entra/user_assessment.py
# Purpose  : Entra User Security Assessment (FAST)
#            - bulk role assignments mapping (1 call)
#            - bulk MFA registration via Reports when permitted (1 call)
#            - sparse per-user MFA probes only when needed
#            - core user properties & activity signals
#            - impact/likelihood/risk scoring per user
#            - built-in role overview with warnings
#            - dashboard KPIs/standouts + severity chart beside summary
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
    "User.Read.All",
    "Directory.Read.All",
    "RoleManagement.Read.Directory",
    # Optional (try/catch): enrich signals if granted
    # "IdentityRiskyUser.Read.All",
    # "Reports.Read.All",                   # enables the bulk MFA report fast path
    # "UserAuthenticationMethod.Read.All",  # enables per-user auth probes fallback
]

# ----------------------- module-local CSS ------------------------
USER_AUDIT_CSS = r"""
.user-audit .ua-clickable { cursor: pointer; }
.user-audit .ua-clickable:hover { background: rgba(255,255,255,.05); }
.user-audit .ua-expander > td { padding: 0; background: #10141b; }
.user-audit .ua-expander-body { padding: 14px 16px; border-top: 1px solid rgba(255,255,255,.08); }

.user-audit table[data-key="users_top"] th,
.user-audit table[data-key="users_top"] td { white-space: nowrap; }
.user-audit .ua-hide { display: none !important; }

.user-audit .ua-name { display:inline-flex; align-items:center; gap:8px; }
.user-audit .ua-chevron { display:inline-block; width:1em; transition: transform .15s ease; opacity:.85; }
.user-audit .ua-open .ua-chevron { transform: rotate(90deg); }

.user-audit .cp-json,
.user-audit .cp-json pre,
.user-audit .cp-json code,
.user-audit .ua-details pre,
.user-audit .ua-details code {
  white-space: pre-wrap !important;  /* allow line wrapping */
  word-break: break-word !important; /* break long tokens if needed */
  overflow-x: hidden !important;
}

.user-audit .ua-details,
.user-audit .ua-details .tab-content,
.user-audit .ua-details .nav-tabs,
.user-audit .ua-details .nav-item,
.user-audit .ua-details .nav-link {
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
}

.user-audit .ua-details pre {
  background: transparent !important;
  color: #ccc !important;
  padding: 4px 0 !important;
  border: none !important;
}

.user-audit .ua-flex { display: grid; grid-template-columns: 1fr 1.2fr; gap: 16px; }
@media (max-width: 1100px){ .user-audit .ua-flex { grid-template-columns: 1fr; } }
.user-audit .ua-pane {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: color-mix(in srgb, var(--card) 94%, #000 6%);
  overflow: hidden;
}
.user-audit .ua-pane h5 {
  margin: 0; padding: 10px 12px;
  background: color-mix(in srgb, var(--accent2) 85%, #000 15%);
  color: #fff; font-weight: 700; border-bottom: 1px solid rgba(0,0,0,.2);
}
.user-audit .ua-kv { width: 100%; border-collapse: collapse; }
.user-audit .ua-kv th, .user-audit .ua-kv td {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top;
}
.user-audit .ua-kv th {
  width: 210px; white-space: nowrap;
  background: color-mix(in srgb, var(--accent2) 12%, var(--card)); font-weight: 600;
}
.user-audit .ua-kv tr:last-child td, .user-audit .ua-kv tr:last-child th { border-bottom: 0; }

.user-audit .ua-toolbar{
  display:flex; gap:10px; align-items:center; margin:6px 2px 0 2px; flex-wrap:wrap;
}
.user-audit .ua-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:220px; outline:none;
}
.user-audit .ua-toolbar .btn{
  padding:6px 12px; border:1px solid var(--border); border-radius:999px;
  background:var(--card); cursor:pointer; font-weight:600;
}
.user-audit .ua-toolbar .btn.primary{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}
/* Keep the User Details table compact */
.user-audit table[data-key="user_details"]{
  table-layout: fixed;            /* prevents any single column from blowing up the width */
}
.user-audit table[data-key="user_details"] th,
.user-audit table[data-key="user_details"] td{
  word-break: break-word;         /* wrap long tokens if they occur */
}
.user-audit table[data-key="user_details"] .cp-json > summary{
  max-width: 520px;               /* don't let the JSON summary expand the column */
}
"""

# ----------------------- module-local JS -------------------------
USER_AUDIT_JS = r"""
(function () {
  const root = document.querySelector('.user-audit') || document;
  const top = root.querySelector('#tbl-users-top');
  const det = root.querySelector('#tbl-user-details');
  if (top) top.setAttribute('data-key','users_top');
  if (det) det.setAttribute('data-key','user_details');
  if (!top) return;

  const txt = el => (el?.textContent || '').trim();
  const slug = s => (s||'').toLowerCase().replace(/[^a-z0-9]+/g,'-');

  function hideColumns(table, keepSet) {
    const HIDE_ALWAYS = new Set(['Id', 'Details']);
    const ths = Array.from(table.querySelectorAll('thead th'));
    const idxByName = new Map();
    ths.forEach((th,i)=> idxByName.set((th.textContent || '').trim(), i));
    ths.forEach((th,i)=>{
      const name = (th.textContent || '').trim();
      if (HIDE_ALWAYS.has(name) || !keepSet.has(name)) {
        table
          .querySelectorAll(`thead th:nth-child(${i+1}), tbody td:nth-child(${i+1})`)
          .forEach(c => c.classList.add('ua-hide'));
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
      const uid = txt(tds[0]);
      const m = {};
      headers.forEach((name, idx) => m[name] = tds[idx] ? tds[idx].innerHTML : '');
      map.set(uid, m);
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
      nameCell.innerHTML = `<span class="ua-name"><span class="ua-chevron">▸</span><span class="ua-label"></span></span>`;
      nameCell.querySelector('.ua-label').textContent = label;

      tr.classList.add('ua-clickable');
      tr.addEventListener('click', ()=>{
        const uid  = idCell ? txt(idCell) : (isDetailsTable ? txt(tr.children[0]) : '');
        const open = tr.classList.contains('ua-open');
        const next = tr.nextElementSibling;
        if (next && next.classList.contains('ua-expander')) next.remove();
        tr.classList.remove('ua-open');
        if (open) return;

        let src = {};
        if (isDetailsTable) {
          const headers = Array.from(table.querySelectorAll('thead th')).map(h=>txt(h));
          const tds = Array.from(tr.children);
          headers.forEach((name, idx) => src[name] = tds[idx] ? tds[idx].innerHTML : '');
        } else {
          src = detailsById.get(uid) || {};
        }

        const upn     = src['UPN'] || '';
        const type    = src['Type'] || '';
        const status  = src['Status'] || '';
        const last    = src['Last Sign-In'] || '';
        const roles   = src['Dir Roles'] || '0';
        const mfa     = src['MFA?'] || 'unknown';
        const details = src['Details'] || '';

        const html = `
          <div class="ua-expander-body">
            <div class="ua-flex">
              <div class="ua-pane">
                <h5>Overview</h5>
                <table class="ua-kv"><tbody>
                  <tr><th>Display Name</th><td>${label}</td></tr>
                  <tr><th>UPN</th><td>${upn}</td></tr>
                  <tr><th>Type</th><td>${type}</td></tr>
                  <tr><th>Status</th><td>${status}</td></tr>
                  <tr><th>Last Sign-In</th><td>${last}</td></tr>
                  <tr><th>Directory Roles</th><td>${roles}</td></tr>
                  <tr><th>MFA Enrolled</th><td>${mfa}</td></tr>
                </tbody></table>
              </div>
              <div class="ua-pane">
                <h5>Details</h5>
                <table class="ua-kv"><tbody>
                  <tr><th>Full Object</th><td>${details || '<em>none available</em>'}</td></tr>
                </tbody></table>
              </div>
            </div>
          </div>`;

        const exp = document.createElement('tr');
        const td  = document.createElement('td');
        exp.className = 'ua-expander';
        td.colSpan = totalCols;
        td.innerHTML = html;
        exp.appendChild(td);
        tr.parentNode.insertBefore(exp, tr.nextSibling);
        tr.classList.add('ua-open');

        const chev = tr.querySelector('.ua-chevron');
        if (chev) chev.textContent = '▾';
      });
    });
  }

  function addToolbar(table, title){
    const card = table.closest('.card');
    if (!card) return;
    const bar = document.createElement('div');
    bar.className = 'ua-toolbar';
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
  const keepTop = new Set(['Id','DisplayName','UPN','Type','Status','Risk']);
  const idxTop  = hideColumns(top, keepTop);
  const detMap  = buildDetailsMap(det);
  attachRowDrawer(top, {
    idIdx: idxTop.has('Id') ? idxTop.get('Id') : -1,
    nameIdx: idxTop.has('DisplayName') ? idxTop.get('DisplayName') : -1,
    detailsById: detMap,
    isDetailsTable: false
  });
  const barTop = addToolbar(top, 'Users Top');
  if (barTop) paginateAndSearch(top, barTop);

  // ------------ Details table (det) ------------
  if (det) {
    const headers = Array.from(det.querySelectorAll('thead th')).map(h=>txt(h));
    const idIdx   = headers.indexOf('Id');
    const nameIdx = headers.indexOf('Display Name');

    // Hide Id and Details columns in the grid (data still exists for the drawer)
    headers.forEach((name, idx)=>{
      if (name === 'Id' || name === 'Details') {
        det.querySelectorAll(`thead th:nth-child(${idx+1}), tbody td:nth-child(${idx+1})`)
           .forEach(c => c.classList.add('ua-hide'));
      }
    });

    attachRowDrawer(det, {
      idIdx: idIdx >= 0 ? idIdx : 0,
      nameIdx: nameIdx >= 0 ? nameIdx : 1,
      detailsById: null,
      isDetailsTable: true
    });

    const barDet = addToolbar(det, 'User Details');
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

def _safe(dtstr):
    try:
        return datetime.fromisoformat(str(dtstr).rstrip("Z")).replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _days_since(dtstr):
    dt = _safe(dtstr)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).days

def _bucket_from_risk(risk: int) -> str:
    if risk is None: return "unknown"
    if risk >= 80:   return "critical"
    if risk >= 40:   return "warning"
    return "ok"

def _impact_likelihood(user: Dict[str, Any], counts: Dict[str,int], signals: Dict[str,Any]) -> Dict[str,int]:
    impact = 1
    if counts.get("dirRoles"): impact += min(20, counts["dirRoles"] * 6)
    if user.get("userType") == "Guest" and counts.get("dirRoles"): impact += 10
    if not user.get("accountEnabled", True): impact = max(impact-1, 0)

    likelihood = 1
    if signals.get("risky"): likelihood += 10
    if signals.get("mfa") is False: likelihood += 6
    last_days = signals.get("lastSignInDays")
    if last_days is not None and last_days > 120: likelihood += 2

    risk = int(impact * max(1, likelihood))
    return {"impact": impact, "likelihood": likelihood, "risk": risk}

# --------------------- Graph helpers (v1.0-safe) ---------------------

def _get_users(client) -> List[Dict[str, Any]]:
    base = "users?$select=id,displayName,userPrincipalName,userType,accountEnabled,createdDateTime,signInActivity"
    try:
        return client.get_all(base)
    except Exception:
        return client.get_all("users?$select=id,displayName,userPrincipalName,userType,accountEnabled,createdDateTime")

def _try_get_risky_users_map(client) -> Dict[str, Dict[str, Any]]:
    try:
        rows = client.get_all("identityProtection/riskyUsers?$select=id,riskLevel,riskState")
        return {r["id"]: r for r in rows}
    except Exception:
        return {}

# -------- Fast-path role mapping (single call) --------
def _get_all_directory_role_assignments(client) -> List[Dict[str, Any]]:
    url = (
        "roleManagement/directory/roleAssignments?"
        "$select=id,principalId,roleDefinitionId,directoryScopeId"
        "&$expand=roleDefinition($select=displayName,isBuiltIn)"
    )
    return client.get_all(url)

def _map_dir_roles_for_users(client) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    """Build {user_id: [roleName,...]} from a single pull of assignments."""
    assigns = _get_all_directory_role_assignments(client)
    roles_by_user: Dict[str, List[str]] = {}
    for a in assigns:
        pid = a.get("principalId")
        name = (a.get("roleDefinition") or {}).get("displayName")
        if not pid or not name:
            continue
        roles_by_user.setdefault(pid, []).append(name)
    return roles_by_user, assigns

# -------- MFA fast path (Reports API) + sparse fallback --------
def _get_auth_report_bulk(client) -> Dict[str, Dict[str, Any]]:
    """
    v1.0 bulk MFA: /reports/authenticationMethods/userRegistrationDetails
    Some tenants don't expose `defaultMethod` in v1.0. Stick to
    id, isMfaRegistered, methodsRegistered and fall back to no $select if needed.
    Returns {userId: {"mfa": bool, "signals": {...}}}
    """
    def parse_rows(rows):
        out = {}
        for r in rows or []:
            methods = [str(m).lower() for m in (r.get("methodsRegistered") or [])]
            sig = {
                "has_auth_app": any("microsoftauthenticator" in m for m in methods),
                "has_fido2": any("fido2" in m for m in methods),
                "has_whfb": any("windowshello" in m for m in methods),
                "has_software_oath": any("softwareoath" in m for m in methods),
                "has_phone": any("phone" in m for m in methods),
                "has_email": any("email" in m for m in methods),
            }
            uid = r.get("id")
            if uid:
                out[uid] = {"mfa": bool(r.get("isMfaRegistered")), "signals": sig}
        return out

    # Try with a minimal $select
    try:
        rows = client.get_all(
            "reports/authenticationMethods/userRegistrationDetails"
            "?$select=id,isMfaRegistered,methodsRegistered"
        )
        return parse_rows(rows)
    except Exception:
        pass

    # Fallback: no $select at all (broader payload but more compatible)
    try:
        rows = client.get_all("reports/authenticationMethods/userRegistrationDetails")
        return parse_rows(rows)
    except Exception:
        return {}

def _try_get_auth_methods(client, uid: str, preview: bool = False) -> Dict[str, bool | None]:
    """
    Best-effort MFA probe using only stable v1.0 by default.
    Set preview=True to probe TAP/X.509 (may 400/404 depending on tenant).
    """
    signals = {
        "has_any": False, "has_auth_app": False, "has_fido2": False, "has_whfb": False,
        "has_software_oath": False, "has_phone": False, "sms_signin_enabled": False,
        "has_email": False, "has_temp_pass": False, "has_x509": False,
    }

    try:
        rows = client.get_all(f"users/{uid}/authentication/methods")
        if isinstance(rows, list):
            signals["has_any"] = len(rows) > 0
            for m in rows:
                t = (m.get("@odata.type") or "").lower()
                if "microsoftauthenticator" in t: signals["has_auth_app"] = True
                elif "fido2" in t: signals["has_fido2"] = True
                elif "windowshelloforbusiness" in t: signals["has_whfb"] = True
                elif "softwareoath" in t: signals["has_software_oath"] = True
                elif ".phonemethod" in t: signals["has_phone"] = True
                elif ".emailmethod" in t: signals["has_email"] = True
                elif "temporaryaccesspass" in t: signals["has_temp_pass"] = True
                elif "x509certificate" in t: signals["has_x509"] = True
    except Exception:
        pass

    stable = [
        ("has_auth_app",      f"users/{uid}/authentication/microsoftAuthenticatorMethods?$select=id"),
        ("has_fido2",         f"users/{uid}/authentication/fido2Methods?$select=id"),
        ("has_whfb",          f"users/{uid}/authentication/windowsHelloForBusinessMethods?$select=id"),
        ("has_software_oath", f"users/{uid}/authentication/softwareOathMethods?$select=id"),
        ("has_email",         f"users/{uid}/authentication/emailMethods?$select=id"),
    ]
    for flag, url in stable:
        if signals[flag]: continue
        try:
            rows = client.get_all(url)
            if isinstance(rows, list) and rows: signals[flag] = True
        except Exception:
            continue

    try:
        rows = client.get_all(f"users/{uid}/authentication/phoneMethods")
        if isinstance(rows, list) and rows:
            signals["has_phone"] = True
            for m in rows:
                if str(m.get("smsSignInState", "")).lower() == "enabled":
                    signals["sms_signin_enabled"] = True
                    break
    except Exception:
        pass

    if preview:
        for flag, url in [
            ("has_temp_pass", f"users/{uid}/authentication/temporaryAccessPassMethods?$select=id"),
            ("has_x509",      f"users/{uid}/authentication/x509CertificateAuthenticationMethods?$select=id"),
        ]:
            if signals[flag]: continue
            try:
                rows = client.get_all(url)
                if isinstance(rows, list) and rows: signals[flag] = True
            except Exception:
                continue

    mfa_present = (
        signals["has_auth_app"] or signals["has_fido2"] or signals["has_whfb"] or
        signals["has_software_oath"] or signals["has_phone"] or signals["has_email"] or
        (preview and (signals["has_temp_pass"] or signals["has_x509"])) or signals["has_any"]
    )
    return {"mfa": mfa_present, "signals": signals}

def _try_get_auth_methods_sparse(client, uid_list: List[str], preview: bool = False) -> Dict[str, Dict[str, Any]]:
    """Probe a limited set of users to avoid O(n) calls when reports aren't available."""
    out: Dict[str, Dict[str, Any]] = {}
    for uid in uid_list:
        try:
            out[uid] = _try_get_auth_methods(client, uid, preview=preview)
        except Exception:
            out[uid] = {"mfa": None, "signals": {}}
    return out

def _summarise_built_in_roles(assignments: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    by_def: Dict[str, Dict[str, Any]] = {}
    for a in assignments:
        rd = a.get("roleDefinition") or {}
        name = rd.get("displayName") or "(unknown role)"
        if not rd.get("isBuiltIn", True): continue
        key = a.get("roleDefinitionId") or name
        rec = by_def.setdefault(key, {"roleName": name, "assigneeIds": set()})
        if a.get("principalId"): rec["assigneeIds"].add(a["principalId"])

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
            "Assignees": count,
            "Threshold": threshold if threshold is not None else "-",
            "Warning": warn_txt or "-",
        })
    rows.sort(key=lambda r: (0 if r["Role"]=="Global Administrator" else 1, -int(r["Assignees"])))
    return rows, warnings

# --------------------- Module entry point -----------------------

def run(client, args):
    run_id = fncNewRunId("users")
    ts = datetime.now(timezone.utc).isoformat()
    fncPrintMessage("Starting module: entra/user_assessment", "info")
    fncPrintMessage(f"Running User Assessment (run={run_id})", "info")

    users = _get_users(client)
    risky_map = _try_get_risky_users_map(client)

    # FAST: map all directory roles once
    roles_by_user, role_assignments_all = _map_dir_roles_for_users(client)

    # FAST: bulk MFA report (if permitted). Otherwise, sparse per-user probes.
    auth_bulk = _get_auth_report_bulk(client)
    preview_auth = bool(getattr(args, "preview_auth_methods", False))
    fast_mode = bool(getattr(args, "fast", False))

    if auth_bulk or fast_mode:
        auth_sparse: Dict[str, Dict[str, Any]] = {}
    else:
        admin_ids = [u["id"] for u in users if roles_by_user.get(u["id"])]
        remainder = [u["id"] for u in users if u["id"] not in admin_ids]
        cap = max(0, 100 - len(admin_ids))
        probe_list = admin_ids + remainder[:cap]
        auth_sparse = _try_get_auth_methods_sparse(client, probe_list, preview=preview_auth)

    overview_rows: List[Dict[str, Any]] = []
    detail_rows:   List[Dict[str, Any]] = []

    # Aggregates for dashboard
    bucket_counts = {"critical":0, "warning":0, "ok":0, "unknown":0}
    guest_count = 0
    disabled_count = 0
    admin_users = 0
    mfa_enabled = 0
    risky_users = 0

    top_risky = None
    top_roles = None
    stalest = None

    for u in users:
        uid = u["id"]
        upn = u.get("userPrincipalName","")
        display = u.get("displayName","")
        utype = u.get("userType") or "Member"
        enabled = bool(u.get("accountEnabled", True))
        guest_count += (1 if utype == "Guest" else 0)
        disabled_count += (0 if enabled else 1)

        dir_role_names = sorted(set(roles_by_user.get(uid, [])))
        dir_roles_count = len(dir_role_names)
        admin_users += (1 if dir_roles_count > 0 else 0)

        auth = auth_bulk.get(uid) or auth_sparse.get(uid) or {"mfa": None, "signals": {}}
        if auth.get("mfa"): mfa_enabled += 1

        risky_row = risky_map.get(uid)
        risky_flag = bool(risky_row)
        risky_users += (1 if risky_flag else 0)

        si = u.get("signInActivity") or {}
        last_sign_in = si.get("lastSignInDateTime") or si.get("lastSuccessfulSignInDateTime")
        last_days = _days_since(last_sign_in)

        counts = { "dirRoles": dir_roles_count }
        signals = { "mfa": auth.get("mfa"), "risky": risky_flag, "lastSignInDays": last_days }
        score = _impact_likelihood(u, counts, signals)
        bucket = _bucket_from_risk(score["risk"])
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        if (top_risky is None) or (score["risk"] > top_risky["risk"]):
            top_risky = {"name": display or upn, "risk": score["risk"], "bucket": bucket}
        if (top_roles is None) or (dir_roles_count > (top_roles["count"] if top_roles else -1)):
            top_roles = {"name": display or upn, "count": dir_roles_count}
        if enabled and last_days is not None:
            if (stalest is None) or (last_days > stalest["days"]):
                stalest = {"name": display or upn, "days": last_days}

        overview_rows.append({
            "Id": uid,
            "DisplayName": display,
            "UPN": upn,
            "Type": utype,
            "Status": "Enabled" if enabled else "Disabled",
            "Risk": score["risk"],
        })

        details_blob = {
            "General": {
                "UPN": upn, "Type": utype, "Enabled": enabled,
                "Created": u.get("createdDateTime"), "LastSignIn": last_sign_in,
                "LastSignInDays": last_days,
            },
            "DirectoryRoles": dir_role_names,
            "Signals": {
                "MFA_Present": auth.get("mfa"),
                "RiskyUser": risky_flag,
                "RiskyMeta": risky_row or {},
            },
            "Score": {
                "impact": score["impact"],
                "likelihood": score["likelihood"],
                "risk": score["risk"],
                "bucket": bucket,
            },
            "Counts": counts,
        }

        detail_rows.append({
            "Id": uid,
            "Display Name": display,
            "UPN": upn,
            "Type": utype,
            "Status": "Enabled" if enabled else "Disabled",
            "Last Sign-In": last_sign_in or "-",
            "Dir Roles": dir_roles_count,
            "MFA?": "Yes" if auth.get("mfa") else ("No" if auth.get("mfa") is False else "Unknown"),
            "Details": details_blob,
        })

    overview_rows.sort(key=lambda r: r["Risk"], reverse=True)

    fncPrintMessage("[•] Users Overview (sorted by risk)", "info")
    print(fncToTable(
        overview_rows,
        headers=["DisplayName","UPN","Type","Status","Risk"],
        max_rows=len(overview_rows),
    ))

    fncPrintMessage("[•] Summarising built-in Entra roles", "info")
    built_in_rows, role_warnings = _summarise_built_in_roles(role_assignments_all)
    if built_in_rows:
        print(fncToTable(
            built_in_rows,
            headers=["Role", "Assignees", "Threshold", "Warning"],
            max_rows=len(built_in_rows),
        ))
    for w in role_warnings:
        fncPrintMessage(w, "warn")

    total_users = len(users)
    severity_labels = ["Critical","Warning","OK","Unknown"]
    severity_values = [
        bucket_counts.get("critical",0),
        bucket_counts.get("warning",0),
        bucket_counts.get("ok",0),
        bucket_counts.get("unknown",0),
    ]

    kpis = [
        {"label":"Total Users","value":str(total_users),"tone":"primary","icon":"bi-people"},
        {"label":"Admins (Dir Roles)","value":str(admin_users),"tone":"danger","icon":"bi-shield-lock"},
        {"label":"Guests","value":str(guest_count),"tone":"info","icon":"bi-person"},
        {"label":"Disabled","value":str(disabled_count),"tone":"secondary","icon":"bi-slash-circle"},
        {"label":"MFA Enrolled","value":str(mfa_enabled),"tone":"success","icon":"bi-phone"},
    ]
    if risky_users:
        kpis.append({"label":"Risky Users","value":str(risky_users),"tone":"warning","icon":"bi-exclamation-triangle"})

    standouts = {}
    if top_risky:
        standouts["group"] = {
            "title":"Highest Risk User",
            "name": top_risky["name"],
            "risk_score": float(min(10.0, top_risky["risk"] / 10.0)),
            "comment": f"{top_risky['bucket'].title()} risk (score {top_risky['risk']})"
        }
    if top_roles:
        standouts["user"] = {
            "title":"Most Directory Roles",
            "name": top_roles["name"],
            "risk_score": float(min(10.0, (top_roles['count'] or 0) * 2)),
            "comment": f"{top_roles['count']} role(s)"
        }
    if stalest and stalest["days"] is not None:
        standouts["computer"] = {
            "title":"Stalest Enabled Account",
            "name": stalest["name"],
            "risk_score": float(min(10.0, stalest["days"] / 36.5)),
            "comment": f"{stalest['days']} days since last sign-in"
        }

    data = {
        "provider": "entra",
        "run_id": run_id,
        "timestamp": ts,
        "summary": {
            "Total Users": total_users,
            "Admins (Directory Roles)": admin_users,
            "Guests": guest_count,
            "Disabled": disabled_count,
            "MFA Enrolled": mfa_enabled,
            "Risky Users (if permitted)": risky_users,
            "Role warnings": len(role_warnings),
        },
        "users_top": overview_rows,
        "user_details": detail_rows,
        "role_assignments_overview": built_in_rows,

        # ===== Dashboard bits =====
        "_kpis": kpis,
        "_standouts": standouts,
        "_charts": {
            "place": "summary",   # render Severity next to Summary (compact)
            "severity": {"labels": severity_labels, "data": severity_values},
        },

        # ===== Scoped styling/behaviour for tables =====
        "_inline_css": USER_AUDIT_CSS,
        "_inline_js":  USER_AUDIT_JS,
        "_container_class": "user-audit",
        "_title": "Entra User Security Assessment",
        "_subtitle": "User posture, admin exposure, MFA & risk signals",
    }

    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "user_assessment", data)

    fncPrintMessage("User Assessment module complete.", "success")
    return data
