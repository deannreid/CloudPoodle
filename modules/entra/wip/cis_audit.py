# ================================================================
# File     : modules/entra/cis_audit.py
# Purpose  : Build a CIS Dashboard for Entra (Azure AD) using
#            module results + CIS rule pack (Level 1 or 2).
#            - Can self-collect required module payloads, OR
#              consume a preloaded JSON of module payloads.
# Output   : data["cis_summary"]   (KV)
#            data["cis_findings"]  (list[dict])
#            data["_kpis"], "_charts", "_standouts" for dashboard
# Notes    : Rules live at rules/cis/{provider}/level{1,2}.json
#            Provider fixed to "entra" in this module path.
# ================================================================

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
import json
import os

from core.utils import fncPrintMessage, fncNewRunId, fncToTable
from core.reporting import fncWriteHTMLReport
from core.cis import load_rules, evaluate_rules

# If you prefer to import via your module loader, you can replace
# direct imports with fncRunModule. Direct imports are faster here.
from modules.entra.tenant_overview import run as run_tenant_overview
from modules.entra.user_assessment import run as run_user_assessment
from modules.entra.group_audit import run as run_group_audit
from app_credentials_expiry import run as run_app_creds

REQUIRED_PERMS: List[str] = [
    # Broad read-only set; some modules also handle missing perms gracefully
    "Directory.Read.All",
    "User.Read.All",
    "Group.Read.All",
    "Application.Read.All",
    "RoleManagement.Read.Directory",
]

# ------------------------- scoped CSS/JS -------------------------

CIS_CSS = r"""
.cis-dash table thead th{ position:sticky; top:0; z-index:1 }
.cis-dash .mt-toolbar{
  display:flex; gap:10px; flex-wrap:wrap; align-items:center;
  margin:6px 2px 0 2px;
}
.cis-dash .mt-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:260px; outline:none;
}
.cis-dash .pill-pass{ background:#1f7a1f22; color:#7ce37c; padding:2px 8px; border-radius:999px; }
.cis-dash .pill-fail{ background:#7a1f1f22; color:#ff8a8a; padding:2px 8px; border-radius:999px; }
.cis-dash .pill-na  { background:#6b6b6b22; color:#cfcfcf; padding:2px 8px; border-radius:999px; }
.cis-dash .wrap { white-space: normal; word-break: break-word; }
"""

CIS_JS = r"""
(function(){
  const root = document.querySelector('.cis-dash') || document;
  const tbl = root.querySelector('#tbl-cis-findings'); if(!tbl) return;
  const card = tbl.closest('.card');
  const bar = document.createElement('div');
  bar.className = 'mt-toolbar';
  bar.innerHTML = `
    <input type="search" placeholder="Search findings…" aria-label="Search findings">
  `;
  card.insertBefore(bar, card.querySelector('.tablewrap'));
  const q = bar.querySelector('input');
  function apply(){
    const s = (q.value||'').toLowerCase();
    for(const tr of tbl.querySelectorAll('tbody tr')){
      tr.style.display = !s || tr.textContent.toLowerCase().includes(s) ? '' : 'none';
    }
  }
  q.addEventListener('input', apply);
})();
"""

# ------------------------- helpers -------------------------

def _provider() -> str:
    # This module is under modules/entra; lock to "entra".
    return "entra"

def _level_from_args(args) -> int:
    try:
        val = getattr(args, "cis", None)
        return int(val) if val else 1
    except Exception:
        return 1

def _load_preloaded_inputs(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            return obj
    except Exception as ex:
        fncPrintMessage(f"Failed to load preloaded CIS inputs from {path}: {ex}", "warn")
    return None

def _collect_required_payloads(client, args) -> Dict[str, Any]:
    """
    Run the minimum set of modules referenced by CIS rules for 'entra'.
    Keys here must match the rule 'source.module' values you use in rules.
    """
    out: Dict[str, Any] = {}

    # Each run() returns its module payload dict
    try:
        fncPrintMessage("[CIS] Collecting: tenant_overview", "info")
        out["tenant_overview"] = run_tenant_overview(client, args)
    except Exception as ex:
        fncPrintMessage(f"[CIS] tenant_overview failed: {ex}", "warn")
        out["tenant_overview"] = {"error": str(ex)}

    try:
        fncPrintMessage("[CIS] Collecting: user_assessment", "info")
        out["user_assessment"] = run_user_assessment(client, args)
    except Exception as ex:
        fncPrintMessage(f"[CIS] user_assessment failed: {ex}", "warn")
        out["user_assessment"] = {"error": str(ex)}

    try:
        fncPrintMessage("[CIS] Collecting: group_audit", "info")
        out["group_audit"] = run_group_audit(client, args)
    except Exception as ex:
        fncPrintMessage(f"[CIS] group_audit failed: {ex}", "warn")
        out["group_audit"] = {"error": str(ex)}

    try:
        fncPrintMessage("[CIS] Collecting: app_credentials_expiry", "info")
        out["app_credentials_expiry"] = run_app_creds(client, args)
    except Exception as ex:
        fncPrintMessage(f"[CIS] app_credentials_expiry failed: {ex}", "warn")
        out["app_credentials_expiry"] = {"error": str(ex)}

    return out

def _status_pill(status: str) -> str:
    s = (status or "").strip().lower()
    if s == "pass": return '<span class="pill-pass">Pass</span>'
    if s == "fail": return '<span class="pill-fail">Fail</span>'
    return '<span class="pill-na">N/A</span>'

# ------------------------- main -------------------------

def run(client, args):
    """
    Normal module entrypoint — collects inputs (or loads preloaded),
    evaluates CIS rules, and emits a dashboard payload.
    CLI options used:
      --cis {1|2}              -> rule level (default 1)
      --cis-inputs <path.json> -> optional preloaded module payloads
    """
    run_id = fncNewRunId("cis")
    ts = datetime.now(timezone.utc).isoformat()
    provider = _provider()
    level = _level_from_args(args)

    fncPrintMessage(f"Starting module: entra/cis_audit (Level {level})", "info")

    # 1) Inputs: preloaded JSON or live collection
    preloaded_path = getattr(args, "cis_inputs", None)
    if isinstance(preloaded_path, str) and os.path.exists(preloaded_path):
        fncPrintMessage(f"[CIS] Using preloaded inputs: {preloaded_path}", "info")
        modules_payloads = _load_preloaded_inputs(preloaded_path) or {}
    else:
        fncPrintMessage("[CIS] No preloaded inputs provided; collecting prerequisites live.", "warn")
        modules_payloads = _collect_required_payloads(client, args)

    # 2) Load rules and evaluate
    ruleset = load_rules(provider, level)
    result = evaluate_rules(modules_payloads, ruleset)

    passed = int(result["counts"].get("passed", 0))
    failed = int(result["counts"].get("failed", 0))
    total  = int(result["counts"].get("total", 0))
    pass_pct = int(round(0 if total == 0 else (passed * 100.0 / total)))

    # 3) Dress findings table
    rows: List[Dict[str, Any]] = []
    for f in result.get("findings", []):
        rows.append({
            "ID": f.get("id",""),
            "Title": f.get("title",""),
            "Severity": f.get("severity",""),
            "Status": _status_pill(f.get("status","")),  # show as pill
            "Reason": f.get("reason",""),
            "Source": f.get("sourceModule",""),
            "Path": f.get("path",""),
            "Remediation": f.get("remediation",""),
            "Tags": ", ".join(f.get("tags",[])),
        })

    # 4) Console preview
    fncPrintMessage("CIS Findings (first 20)", "info")
    if rows:
        print(fncToTable(
            [{"ID": r["ID"], "Title": r["Title"], "Severity": r["Severity"], "Status": r["Status"].strip("<>/span class=\"pill-passfailna ")[:4], "Source": r["Source"]} for r in rows[:20]],
            headers=["ID","Title","Severity","Status","Source"],
            max_rows=min(20,len(rows))
        ))
    else:
        print("(no findings)")

    # 5) KPIs & charts
    kpis = [
        {"label":"Total Rules","value":str(total),"tone":"primary","icon":"bi-list-check"},
        {"label":"Passed","value":str(passed),"tone":"success","icon":"bi-check-circle"},
        {"label":"Failed","value":str(failed),"tone":"danger","icon":"bi-x-circle"},
        {"label":"Pass %","value":f"{pass_pct}%","tone":"secondary","icon":"bi-graph-up"},
    ]
    charts = {
        "place": "summary",
        "cis_passfail": {
            "labels": ["Passed","Failed"],
            "data": [passed, failed]
        }
    }

    data = {
        "provider": provider,
        "run_id": run_id,
        "timestamp": ts,
        "summary": {
            "CIS Profile": f"Level {level}",
            "Provider": provider,
            "Rules Evaluated": total,
            "Passed": passed,
            "Failed": failed,
            "Pass %": f"{pass_pct}%",
        },

        # sections rendered by reporter
        "cis_summary": [
            {"Field":"CIS Profile","Value":f"Level {level}"},
            {"Field":"Provider","Value":provider},
            {"Field":"Rules Evaluated","Value":total},
            {"Field":"Passed","Value":passed},
            {"Field":"Failed","Value":failed},
            {"Field":"Pass %","Value":f"{pass_pct}%"},
        ],
        "cis_findings": rows,

        # dashboard bits
        "_kpis": kpis,
        "_charts": charts,
        "_container_class": "cis-dash",
        "_inline_css": CIS_CSS,
        "_inline_js":  CIS_JS,
        "_title": f"CIS Dashboard — Level {level}",
        "_subtitle": "Rule evaluation across collected module results",
    }

    # Optional one-off HTML
    if getattr(args, "html", None):
        out = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(out, "cis_audit", data)

    fncPrintMessage("CIS Audit module complete.", "success")
    return data
