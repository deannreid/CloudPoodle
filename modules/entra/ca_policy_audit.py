# ================================================================
# File     : modules/entra/ca_audit.py
# Purpose  : Conditional Access (CA) policy audit for Entra ID
#            - enumerate CA policies (v1.0, fallback to beta)
#            - identify overly-permissive / weak policies
#            - score impact/likelihood to rank risk
# Output   : data["ca_policies"] (overview)
#            data["ca_policy_details"] (per-policy details)
#            data["_kpis"], data["_standouts"], data["_charts"]
#            data["_inline_css"] / "_inline_js" / "_container_class"
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
    "Directory.Read.All",
    "Policy.Read.All",                # or Policy.Read.ConditionalAccess
]

# ----------------------- module-local CSS ------------------------
CA_AUDIT_CSS = r"""
.ca-audit .ca-clickable { cursor: pointer; }
.ca-audit .ca-clickable:hover { background: rgba(255,255,255,.05); }
.ca-audit .ca-expander > td { padding: 0; background: #10141b; }
.ca-audit .ca-expander-body { padding: 14px 16px; border-top: 1px solid rgba(255,255,255,.08); }

/* Overview table: keep columns tight and only show selected ones */
.ca-audit table[data-key="ca_policies"] th,
.ca-audit table[data-key="ca_policies"] td { white-space: nowrap; }
.ca-audit .ca-hide { display: none !important; }

/* Chevron in Name cell */
.ca-audit .ca-name { display:inline-flex; align-items:center; gap:8px; }
.ca-audit .ca-chevron { display:inline-block; width:1em; transition: transform .15s ease; opacity:.85; }
.ca-audit .ca-open .ca-chevron { transform: rotate(90deg); }

/* Drawer layout */
.ca-audit .ca-flex { display: grid; grid-template-columns: 1fr 1.2fr; gap: 16px; }
@media (max-width: 1100px){ .ca-audit .ca-flex { grid-template-columns: 1fr; } }
.ca-audit .ca-pane {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: color-mix(in srgb, var(--card) 94%, #000 6%);
  overflow: hidden;
}
.ca-audit .ca-pane h5 {
  margin: 0; padding: 10px 12px;
  background: color-mix(in srgb, var(--accent2) 85%, #000 15%);
  color: #fff; font-weight: 700; border-bottom: 1px solid rgba(0,0,0,.2);
}
.ca-audit .ca-kv { width: 100%; border-collapse: collapse; }
.ca-audit .ca-kv th, .ca-audit .ca-kv td {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top;
}
.ca-audit .ca-kv th {
  width: 210px; white-space: nowrap;
  background: color-mix(in srgb, var(--accent2) 12%, var(--card)); font-weight: 600;
}
.ca-audit .ca-kv tr:last-child td, .ca-audit .ca-kv tr:last-child th { border-bottom: 0; }

/* Toolbar (search + view more) */
.ca-audit .ca-toolbar{
  display:flex; gap:10px; align-items:center; margin:6px 2px 0 2px; flex-wrap:wrap;
}
.ca-audit .ca-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:220px; outline:none;
}
.ca-audit .ca-toolbar .btn{
  padding:6px 12px; border:1px solid var(--border); border-radius:999px;
  background:var(--card); cursor:pointer; font-weight:600;
}
.ca-audit .ca-toolbar .btn.primary{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}

/* Wrap long JSON in details cell */
.ca-audit .cp-json, .ca-audit .cp-json pre, .ca-audit .cp-json code {
  white-space: pre-wrap !important; word-break: break-word !important; overflow-x: hidden !important;
}
.ca-audit .chart-container canvas {
  max-height: 240px !important;
  width: 100% !important;
}
"""

# ----------------------- module-local JS -------------------------
CA_AUDIT_JS = r"""
(function () {
  const root = document.querySelector('.ca-audit') || document;

  // Known table ids from reporting.py
  const top = root.querySelector('#tbl-ca-policies');
  const det = root.querySelector('#tbl-ca-policy-details');
  if (top) top.setAttribute('data-key','ca_policies');
  if (det) det.setAttribute('data-key','ca_policy_details');
  if (!top) return;

  // ---- helpers ----
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
        table.querySelectorAll(`thead th:nth-child(${i+1}), tbody td:nth-child(${i+1})`)
             .forEach(c => c.classList.add('ca-hide'));
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
      const id = txt(tds[0]);
      const m = {};
      headers.forEach((name, idx) => m[name] = tds[idx] ? tds[idx].innerHTML : '');
      map.set(id, m);
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
      nameCell.innerHTML = `<span class="ca-name"><span class="ca-chevron">▸</span><span class="ca-label"></span></span>`;
      nameCell.querySelector('.ca-label').textContent = label;

      tr.classList.add('ca-clickable');
      tr.addEventListener('click', ()=>{
        const pid  = idCell ? txt(idCell) : (isDetailsTable ? txt(tr.children[0]) : '');
        const open = tr.classList.contains('ca-open');
        const next = tr.nextElementSibling;
        if (next && next.classList.contains('ca-expander')) next.remove();
        tr.classList.remove('ca-open');
        if (open) return;

        let src = {};
        if (isDetailsTable) {
          const headers = Array.from(table.querySelectorAll('thead th')).map(h=>txt(h));
          const tds = Array.from(tr.children);
          headers.forEach((name, idx) => src[name] = tds[idx] ? tds[idx].innerHTML : '');
        } else {
          src = detailsById.get(pid) || {};
        }

        const state   = src['State'] || '';
        const apps    = src['Apps'] || '';
        const users   = src['Users'] || '';
        const grants  = src['Grant'] || '';
        const sess    = src['Session'] || '';
        const notes   = src['Notes'] || '';
        const details = src['Details'] || '';

        const html = `
          <div class="ca-expander-body">
            <div class="ca-flex">
              <div class="ca-pane">
                <h5>Overview</h5>
                <table class="ca-kv"><tbody>
                  <tr><th>Name</th><td>${label}</td></tr>
                  <tr><th>Id</th><td>${pid || '<em>unknown</em>'}</td></tr>
                  <tr><th>State</th><td>${state}</td></tr>
                  <tr><th>Users/Groups</th><td>${users}</td></tr>
                  <tr><th>Apps</th><td>${apps}</td></tr>
                  <tr><th>Grant Controls</th><td>${grants}</td></tr>
                  <tr><th>Session</th><td>${sess}</td></tr>
                  <tr><th>Notes</th><td>${notes || '-'}</td></tr>
                </tbody></table>
              </div>
              <div class="ca-pane">
                <h5>Details</h5>
                <table class="ca-kv"><tbody>
                  <tr><th>Full Object</th><td>${details || '<em>none available</em>'}</td></tr>
                </tbody></table>
              </div>
            </div>
          </div>`;

        const exp = document.createElement('tr');
        const td  = document.createElement('td');
        exp.className = 'ca-expander';
        td.colSpan = totalCols;
        td.innerHTML = html;
        exp.appendChild(td);
        tr.parentNode.insertBefore(exp, tr.nextSibling);
        tr.classList.add('ca-open');

        const chev = tr.querySelector('.ca-chevron');
        if (chev) chev.textContent = '▾';
      });
    });
  }

  function addToolbar(table, title){
    const card = table.closest('.card');
    if (!card) return;
    const bar = document.createElement('div');
    bar.className = 'ca-toolbar';
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

  // ------------ Overview table (top) ------------
  const keepTop = new Set(['Id','Name','State','Scope','Risk']);
  const idxTop  = hideColumns(top, keepTop);
  const detMap  = buildDetailsMap(det);
  attachRowDrawer(top, {
    idIdx: idxTop.has('Id') ? idxTop.get('Id') : -1,
    nameIdx: idxTop.has('Name') ? idxTop.get('Name') : -1,
    detailsById: detMap,
    isDetailsTable: false
  });
  const barTop = addToolbar(top, 'CA Policies');
  if (barTop) paginateAndSearch(top, barTop);

  // ------------ Details table (det) ------------
  if (det) {
    const headers = Array.from(det.querySelectorAll('thead th')).map(h=>txt(h));
    const idIdx   = headers.indexOf('Id');
    const nameIdx = headers.indexOf('Name');

    // Hide Id and Details columns in the grid (data remains for the drawer)
    headers.forEach((name, idx)=>{
      if (name === 'Id' || name === 'Details') {
        det.querySelectorAll(`thead th:nth-child(${idx+1}), tbody td:nth-child(${idx+1})`)
          .forEach(c => c.classList.add('ca-hide'));
      }
    });

    attachRowDrawer(det, {
      idIdx: idIdx >= 0 ? idIdx : 0,
      nameIdx: nameIdx >= 0 ? nameIdx : 1,
      detailsById: null,
      isDetailsTable: true
    });

    const barDet = addToolbar(det, 'CA Policy Details');
    if (barDet) paginateAndSearch(det, barDet);
  }
})();
"""

# ----------------------- helpers & scoring -----------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _norm_bool(v) -> bool:
    s = str(v).strip().lower()
    return s in ("true","1","yes","enabled")

def _safe_list(x):
    return x if isinstance(x, list) else []

def _scope_summary(assignments: Dict[str, Any]) -> str:
    if not isinstance(assignments, dict):
        return "-"
    users = assignments.get("users") or {}
    include_all = bool(users.get("includeUsers") == ["All"])
    exc = _safe_list(users.get("excludeUsers"))
    targets = []
    if include_all: targets.append("All users")
    inc_groups = _safe_list(users.get("includeGroups"))
    if inc_groups: targets.append(f"+{len(inc_groups)} group(s)")
    inc_roles = _safe_list(users.get("includeRoles"))
    if inc_roles: targets.append(f"+{len(inc_roles)} role(s)")
    if exc: targets.append(f"-{len(exc)} exclude")
    return ", ".join(targets) or "-"

def _apps_summary(conditions) -> str:
    if not isinstance(conditions, dict):
        return "-"
    ca = conditions.get("applications") or {}
    if not isinstance(ca, dict):
        return "-"
    included = _safe_list(ca.get("includeApplications"))
    include_all = (included == ["All"])
    exc = _safe_list(ca.get("excludeApplications"))
    if include_all and not exc:
        return "All apps"
    parts = []
    if include_all: parts.append("All apps")
    if included and not include_all: parts.append(f"{len(included)} app(s)")
    if exc: parts.append(f"-{len(exc)} exclude")
    return ", ".join(parts) or "-"


def _grant_summary(grant_controls: Dict[str, Any]) -> str:
    if not isinstance(grant_controls, dict):
        return "-"
    op = grant_controls.get("operator") or "OR"
    req = _safe_list(grant_controls.get("builtInControls"))
    custom = _safe_list(grant_controls.get("customAuthenticationFactors"))
    if grant_controls.get("builtInControls") is None and grant_controls.get("customAuthenticationFactors") is None:
        return f"Allow (no controls)"
    parts = []
    if req: parts.append(", ".join(req))
    if custom: parts.append(f"custom:{', '.join(custom)}")
    if not parts:
        parts = ["Allow (no controls)"]
    return f"{' + '.join(parts)} ({op})"

def _session_summary(session_controls) -> str:
    """
    Summarize session controls. Robust to None/non-dict shapes.
    Emits short flags:
      AER = Application Enforced Restrictions
      SIF = Sign-in Frequency
      PB  = Persistent Browser
      CAE = Continuous Access Evaluation
      MCAS = Defender for Cloud Apps (legacy 'cloudAppSecurity')
    """
    if not isinstance(session_controls, dict):
        return "-"

    def _is_enabled(d, key) -> bool:
        v = d.get(key)
        return isinstance(v, dict) and _norm_bool(v.get("isEnabled"))

    flags = []
    if _is_enabled(session_controls, "applicationEnforcedRestrictions"): flags.append("AER")
    if _is_enabled(session_controls, "signInFrequency"):                  flags.append("SIF")
    if _is_enabled(session_controls, "persistentBrowser"):                flags.append("PB")
    if _is_enabled(session_controls, "continuousAccessEvaluation"):       flags.append("CAE")
    if _is_enabled(session_controls, "cloudAppSecurity"):                 flags.append("MCAS")

    return ", ".join(flags) or "-"

def _policy_notes(p: Dict[str, Any]) -> List[str]:
    """
    Heuristics to flag overly-permissive or weak CA policies.
    """
    notes = []
    state = (p.get("state") or "").lower()
    cond = p.get("conditions") or {}
    apps = cond.get("applications") or {}
    users = (p.get("conditions") or {}).get("users") or {}
    grant = p.get("grantControls") or {}
    session = p.get("sessionControls")
    if not isinstance(session, dict):
        notes.append("No session hardening (SIF/AER/CAE/MCAS)")
    else:
        has_enabled = False
        for k, v in session.items():
            if isinstance(v, dict) and _norm_bool(v.get("isEnabled")):
                has_enabled = True
                break
        if not has_enabled:
            notes.append("No session hardening (SIF/AER/CAE/MCAS)")


    all_users = users.get("includeUsers") == ["All"]
    exclude_users = _safe_list(users.get("excludeUsers"))
    all_apps = apps.get("includeApplications") == ["All"]
    grant_controls = _safe_list(grant.get("builtInControls"))

    # Disabled or report-only
    if state == "disabled":
        notes.append("Policy is disabled")
    if state == "enabledforreportingbutnotenforced":
        notes.append("Policy is report-only (not enforced)")

    # Overly permissive core pattern
    if all_users and all_apps and (not grant_controls):
        notes.append("All users + All apps + Allow (no controls)")

    if all_users and all_apps and ("mfa" not in [x.lower() for x in grant_controls]):
        notes.append("All users + All apps without MFA")

    # Very broad exclusions
    if all_users and exclude_users and len(exclude_users) > 0:
        notes.append(f"All users with {len(exclude_users)} exclusion(s) - verify excludes")

    # No session controls at all
    if not session or all(not _norm_bool(v.get("isEnabled")) for v in session.values() if isinstance(v, dict)):
        notes.append("No session hardening (SIF/AER/CAE/MCAS)")

    # Client apps condition absent (legacy protocols might slip)
    client_apps = (cond.get("clientAppTypes") or [])
    if not client_apps:
        notes.append("Client app types not scoped (legacy protocols may bypass)")

    # Locations condition not used
    locs = cond.get("locations") or {}
    if not _safe_list(locs.get("includeLocations")) and not _safe_list(locs.get("excludeLocations")):
        notes.append("No named location scoping")

    # Device platform/state/Compliant filters are commonly forgotten
    device_filter = (cond.get("devices") or {}).get("deviceFilter") or {}
    if not device_filter.get("rule"):
        notes.append("No device filter (e.g., isCompliant)")

    # Sign-in risk / user risk controls missing (orgs that licensed risk features)
    sign_in_risk = cond.get("signInRiskLevels") or []
    user_risk    = cond.get("userRiskLevels") or []
    if not sign_in_risk and not user_risk:
        notes.append("No risk-based condition")

    return notes

def _risk_score(p: Dict[str, Any], notes: List[str]) -> int:
    """
    Quick impact x likelihood heuristic for ordering.
    """
    impact = 0
    cond = p.get("conditions") or {}
    apps = cond.get("applications") or {}
    users = (p.get("conditions") or {}).get("users") or {}
    all_users = users.get("includeUsers") == ["All"]
    all_apps = apps.get("includeApplications") == ["All"]

    if all_users: impact += 20
    if all_apps:  impact += 20
    if (p.get("state") or "").lower() == "enabled": impact += 10

    likelihood = 1
    if any("no controls" in n.lower() for n in notes): likelihood += 10
    if any("without mfa" in n.lower() for n in notes): likelihood += 6
    if any("report-only" in n.lower() for n in notes): likelihood += 2

    return int(max(1, impact) * max(1, likelihood))

def _bucket_from_risk(risk: int) -> str:
    if risk is None: return "unknown"
    if risk >= 200:  return "critical"
    if risk >= 80:   return "warning"
    return "ok"

# --------------------- Graph helpers (v1.0-first) ---------------------

def _get_policies_v1(client) -> List[Dict[str, Any]]:
    # https://graph.microsoft.com/v1.0/identity/conditionalAccess/policies
    return client.get_all("identity/conditionalAccess/policies")

def _get_policies_beta(client) -> List[Dict[str, Any]]:
    # Fallback to beta if tenant doesn’t expose all fields in v1.0
    # The client's get_all always prefixes v1.0; we call .get for full URL.
    fncPrintMessage("Falling back to /beta for CA policies.", "warn")
    return client.get("https://graph.microsoft.com/beta/identity/conditionalAccess/policies").get("value", [])

def _get_policies(client) -> List[Dict[str, Any]]:
    try:
        return _get_policies_v1(client)
    except Exception as ex:
        fncPrintMessage(f"CA policies v1.0 failed: {ex}", "warn")
        try:
            return _get_policies_beta(client)
        except Exception as ex2:
            fncPrintMessage(f"CA policies beta failed: {ex2}", "error")
            return []

# --------------------- Module entry point -----------------------

def run(client, args):
    run_id = fncNewRunId("ca")
    ts = datetime.now(timezone.utc).isoformat()

    policies = _get_policies(client) or []

    overview_rows: List[Dict[str, Any]] = []
    detail_rows:   List[Dict[str, Any]] = []

    bucket_counts = {"critical":0, "warning":0, "ok":0, "unknown":0}
    enabled_count = 0
    report_only   = 0
    disabled_count= 0
    flagged_count = 0

    top_risky = None

    for p in policies:
        pid   = p.get("id","")
        name  = p.get("displayName","")
        state = (p.get("state") or "").replace("enabledForReportingButNotEnforced","ReportOnly")
        cond  = p.get("conditions") or {}
        grant = p.get("grantControls") or {}
        sess = p.get("sessionControls")
        if not isinstance(sess, dict):
            sess = {}

        if state.lower() == "enabled": enabled_count += 1
        elif state.lower() == "disabled": disabled_count += 1
        else: report_only += 1

        scope = _scope_summary(p.get("conditions",{}).get("users") and p.get("conditions") or p.get("conditions"))
        # (The above line keeps format similar to other modules; safe either way.)
        scope = _scope_summary(p.get("conditions") or {})
        apps  = _apps_summary(cond)
        grants= _grant_summary(grant)
        sesst = _session_summary(sess)

        notes = _policy_notes(p)
        if notes: flagged_count += 1
        risk = _risk_score(p, notes)
        bucket = _bucket_from_risk(risk)
        bucket_counts[bucket] = bucket_counts.get(bucket,0)+1

        if (top_risky is None) or (risk > top_risky["risk"]):
            top_risky = {"name": name or pid, "risk": risk, "bucket": bucket}

        # ---- Overview ----
        overview_rows.append({
            "Id": pid,
            "Name": name,
            "State": state or "-",
            "Scope": scope,
            "Risk": risk,
        })

        # ---- Details ----
        details_blob = {
            "General": {
                "Id": pid, "Name": name, "State": state or "-",
            },
            "Assignments": p.get("conditions",{}).get("users") or {},
            "Applications": (p.get("conditions",{}).get("applications") or {}),
            "Conditions": cond,
            "GrantControls": grant,
            "SessionControls": sess,
            "Notes": notes,
            "Score": {"risk": risk, "bucket": bucket},
        }

        detail_rows.append({
            "Id": pid,
            "Name": name,
            "State": state or "-",
            "Users": _scope_summary(p.get("conditions") or {}),
            "Apps": apps,
            "Grant": grants,
            "Session": sesst,
            "Notes": "; ".join(notes),
            "Details": details_blob,
        })

    # Sort overview by risk desc
    overview_rows.sort(key=lambda r: r["Risk"], reverse=True)

    # Console table
    fncPrintMessage("[•] CA Policies (sorted by risk)", "info")
    print(fncToTable(
        overview_rows,
        headers=["Name","State","Scope","Risk"],
        max_rows=len(overview_rows),
    ))

    # ---------- Dashboard content ----------
    total = len(policies)
    kpis = [
        {"label":"Total Policies","value":str(total),"tone":"primary","icon":"bi-list-task"},
        {"label":"Enabled","value":str(enabled_count),"tone":"success","icon":"bi-toggle-on"},
        {"label":"Report-only","value":str(report_only),"tone":"warning","icon":"bi-clipboard-data"},
        {"label":"Disabled","value":str(disabled_count),"tone":"secondary","icon":"bi-toggle-off"},
        {"label":"Flagged (Notes)","value":str(flagged_count),"tone":"danger","icon":"bi-exclamation-triangle"},
    ]

    standouts = {}
    if top_risky:
        standouts["group"] = {
            "title":"Highest Risk CA Policy",
            "name": top_risky["name"],
            "risk_score": float(min(10.0, top_risky["risk"] / 20.0)),
            "comment": f"{top_risky['bucket'].title()} (score {top_risky['risk']})"
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
            "Total Policies": total,
            "Enabled": enabled_count,
            "Report-only": report_only,
            "Disabled": disabled_count,
            "Flagged (Notes)": flagged_count,
        },
        "ca_policies": overview_rows,
        "ca_policy_details": detail_rows,

        # ===== Dashboard bits =====
        "_kpis": kpis,
        "_standouts": standouts,

        # ===== Scoped styling/behaviour =====
        "_inline_css": CA_AUDIT_CSS,
        "_inline_js":  CA_AUDIT_JS,
        "_container_class": "ca-audit",
        "_title": "Conditional Access Audit",
        "_subtitle": "Detect overly-permissive or weak CA policies",
    }

    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "ca_audit", data)

    fncPrintMessage("Conditional Access Audit module complete.", "success")
    return data
