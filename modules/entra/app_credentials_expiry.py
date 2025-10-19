# ================================================================
# File     : app_credentials_expiry.py
# Purpose  : Enumerate Entra App/Service Principal credentials and
#            identify expired / soon-to-expire secrets & certificates.
# Notes    : Adds coloured output for expiring credentials (<30 orange, <10 red)
# ================================================================

from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
from colorama import Fore, Style

from core.utils import (
    fncPrintMessage,
    fncToTable,
    fncNewRunId,
)
from core.reporting import fncWriteHTMLReport

REQUIRED_PERMS = ["Directory.Read.All", "Application.Read.All"]

# ----------------------- Module-local CSS/JS ---------------------

APP_CREDS_CSS = r"""
/* Scope to this module only */
.app-creds .card table { table-layout: auto }

/* Quick filters above each table */
.app-creds .ac-toolbar{
  display:flex; gap:10px; align-items:center; margin:6px 2px 0 2px; flex-wrap:wrap;
}
.app-creds .ac-toolbar input[type="search"]{
  padding:6px 10px; border-radius:999px; border:1px solid var(--border);
  background:var(--card); color:var(--text); min-width:220px; outline:none;
}
.app-creds .ac-toolbar .btn{
  padding:6px 10px; border:1px solid var(--border); border-radius:999px; color: #1f7ae0;
  background:var(--card); cursor:pointer; font-weight:600;
}
.app-creds .ac-toolbar .btn.active{
  background:linear-gradient(90deg,var(--accent2),var(--accent));
  color:#fff; border-color:transparent;
}

/* Make days/bucket columns slightly narrower (reporter already adds pills) */
.app-creds td.col-days, .app-creds th.col-days { text-align:center; width:90px }
.app-creds td.col-buck, .app-creds th.col-buck { text-align:center; width:110px }

/* Sticky header helps with long lists */
.app-creds table thead th{ position:sticky; top:0; z-index:2 }
"""

APP_CREDS_JS = r"""
/* Small QoL for credentials tables: search + bucket quick filters */
(function(){
  const root = document.querySelector('.app-creds') || document;

  function enhanceTable(title){
    const slug = title.toLowerCase().replace(/[^a-z0-9]+/g,'-');
    const card = root.querySelector('#tbl-'+slug)?.closest('.card');
    const tbl  = root.querySelector('#tbl-'+slug);
    if(!card || !tbl) return;

    // toolbar
    const bar = document.createElement('div');
    bar.className = 'ac-toolbar';
    bar.innerHTML = `
      <input type="search" placeholder="Search ${title}…" aria-label="Search ${title}">
      <button class="btn" data-bucket="all">All</button>
      <button class="btn" data-bucket="expired">Expired</button>
      <button class="btn" data-bucket="critical">Critical</button>
      <button class="btn" data-bucket="warning">Warning</button>
      <button class="btn" data-bucket="≤60d">≤60d</button>
      <button class="btn" data-bucket="≤90d">≤90d</button>
      <button class="btn" data-bucket=">90d">>90d</button>
      <button class="btn" data-bucket="unknown">Unknown</button>
    `;
    card.insertBefore(bar, card.querySelector('.tablewrap'));

    const search = bar.querySelector('input[type="search"]');
    const buttons = Array.from(bar.querySelectorAll('.btn'));
    const setActive = b => buttons.forEach(x=>x.classList.toggle('active', x===b));

    function apply(){
      const q = (search.value||'').toLowerCase();
      const active = buttons.find(b=>b.classList.contains('active'));
      const bucket = active ? active.getAttribute('data-bucket') : 'all';
      const head = Array.from(tbl.querySelectorAll('thead th')).map(th=>th.textContent.trim());
      const buckIdx = head.indexOf('bucket');

      Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr=>{
        const txt = tr.textContent.toLowerCase();
        const matchText = !q || txt.includes(q);
        const matchBucket = (bucket==='all') || (buckIdx>=0 && (tr.children[buckIdx]?.textContent||'').trim() === bucket);
        tr.style.display = (matchText && matchBucket) ? '' : 'none';
      });
    }

    buttons[0].classList.add('active'); // All
    buttons.forEach(b=>b.addEventListener('click', ()=>{ setActive(b); apply(); }));
    search.addEventListener('input', apply);
  }

  enhanceTable('Applications');
  enhanceTable('ServicePrincipals');
})();
"""

# ================================================================
# Helper Functions
# ================================================================

def _parse_dt(val):
    if not val:
        return None
    try:
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        s = str(val).rstrip("Z")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _days_left(end):
    if not end:
        return None
    now = datetime.now(timezone.utc)
    delta = end - now
    return int(delta.days)

def _bucket(days):
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= 10:
        return "critical"
    if days <= 30:
        return "warning"
    if days <= 60:
        return "≤60d"
    if days <= 90:
        return "≤90d"
    return ">90d"

def _colour_days(days):
    """Return coloured string depending on days left."""
    if days is None:
        return "-"
    try:
        d = int(days)
    except Exception:
        return str(days)
    if d < 0:   return f"{Fore.LIGHTBLACK_EX}{d}{Style.RESET_ALL}"
    if d < 10:  return f"{Fore.RED}{d}{Style.RESET_ALL}"
    if d < 30:  return f"{Fore.YELLOW}{d}{Style.RESET_ALL}"
    return f"{Fore.WHITE}{d}{Style.RESET_ALL}"

def _flatten_creds(obj: Dict[str, Any], obj_type: str) -> List[Dict[str, Any]]:
    rows = []
    name = obj.get("displayName") or obj.get("appDisplayName") or obj.get("appId") or obj.get("id")
    obj_id = obj.get("id"); app_id = obj.get("appId")

    for p in (obj.get("passwordCredentials") or []):
        end = _parse_dt(p.get("endDateTime")); days = _days_left(end)
        rows.append({
            "objectType": obj_type,
            "objectName": name,
            "appId": app_id or "",
            "objectId": obj_id,
            "credential": "Secret",
            "credDisplayName": p.get("displayName") or "",
            "endDate": p.get("endDateTime") or "",
            "daysRemaining": days,
            "bucket": _bucket(days),
            "keyId": p.get("keyId") or "",
        })

    for k in (obj.get("keyCredentials") or []):
        end = _parse_dt(k.get("endDateTime")); days = _days_left(end)
        rows.append({
            "objectType": obj_type,
            "objectName": name,
            "appId": app_id or "",
            "objectId": obj_id,
            "credential": "Certificate",
            "credDisplayName": k.get("displayName") or "",
            "endDate": k.get("endDateTime") or "",
            "daysRemaining": days,
            "bucket": _bucket(days),
            "keyId": k.get("keyId") or "",
        })
    return rows

def _rows_for_console(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a version with colored days for terminal preview."""
    out = []
    for r in rows:
        r2 = dict(r)
        r2["daysRemaining"] = _colour_days(r.get("daysRemaining"))
        out.append(r2)
    return out

def _summarise(rows: List[Dict[str, Any]]):
    total = len(rows)
    counts = {"expired": 0, "critical": 0, "warning": 0, "≤60d": 0, "≤90d": 0, ">90d": 0, "unknown": 0}
    for r in rows:
        counts[r.get("bucket","unknown")] = counts.get(r.get("bucket","unknown"), 0) + 1
    return {
        "Total Credentials": total,
        "Expired": counts["expired"],
        "Critical (<10d)": counts["critical"],
        "Warning (<30d)": counts["warning"],
        "≤60 days": counts["≤60d"],
        "≤90 days": counts["≤90d"],
        ">90 days": counts[">90d"],
        "Unknown": counts["unknown"],
    }

def _sort_days_key(r: Dict[str, Any]) -> Tuple[int, int]:
    """Sort by bucket severity then days (Unknown at end). Lower is riskier."""
    b_order = {"expired": 0, "critical": 1, "warning": 2, "≤60d": 3, "≤90d": 4, ">90d": 5, "unknown": 6}
    bucket = r.get("bucket","unknown")
    days = r.get("daysRemaining")
    days_num = days if isinstance(days, int) else 99999
    return (b_order.get(bucket, 6), days_num)

# ================================================================
# Main Function
# ================================================================
def run(client, args):
    run_id = fncNewRunId("appcreds")
    fncPrintMessage(f"Running App Credentials Expiry (run={run_id})", "info")

    # Fetch applications
    try:
        apps = client.get_all("applications?$select=id,displayName,appId,passwordCredentials,keyCredentials")
    except Exception:
        fncPrintMessage("Retrying applications without $select (tenant schema variance).", "warn")
        apps = client.get_all("applications")

    # Fetch service principals
    try:
        sps = client.get_all("servicePrincipals?$select=id,displayName,appId,passwordCredentials,keyCredentials")
    except Exception:
        fncPrintMessage("Retrying service principals without $select (tenant schema variance).", "warn")
        sps = client.get_all("servicePrincipals")

    app_rows: List[Dict[str, Any]] = []
    for a in apps:
        app_rows.extend(_flatten_creds(a, "Application"))

    sp_rows: List[Dict[str, Any]] = []
    for s in sps:
        sp_rows.extend(_flatten_creds(s, "ServicePrincipal"))

    # Sort by severity then days asc
    app_rows.sort(key=_sort_days_key)
    sp_rows.sort(key=_sort_days_key)

    # Summaries
    app_summary = _summarise(app_rows)
    sp_summary = _summarise(sp_rows)

    fncPrintMessage("Applications — Credential Expiry Summary", "info")
    print(fncToTable(
        [{"Field": k, "Value": v} for k, v in app_summary.items()],
        headers=["Field", "Value"], max_rows=9999
    ))

    fncPrintMessage("Service Principals — Credential Expiry Summary", "info")
    print(fncToTable(
        [{"Field": k, "Value": v} for k, v in sp_summary.items()],
        headers=["Field", "Value"], max_rows=9999
    ))

    if app_rows:
        fncPrintMessage("Applications — Expiring/Expired (top 25)", "info")
        print(fncToTable(
            _rows_for_console(app_rows)[:25],
            headers=["objectName","appId","credential","credDisplayName","endDate","daysRemaining","bucket"],
            max_rows=25
        ))

    if sp_rows:
        fncPrintMessage("Service Principals — Expiring/Expired (top 20)", "info")
        print(fncToTable(
            _rows_for_console(sp_rows)[:20],
            headers=["objectName","appId","credential","credDisplayName","endDate","daysRemaining","bucket"],
            max_rows=20
        ))

    data = {
        "provider": "entra",
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "Total Credentials": app_summary["Total Credentials"] + sp_summary["Total Credentials"],
            "Expired": app_summary["Expired"] + sp_summary["Expired"],
            "Critical (<10d)": app_summary["Critical (<10d)"] + sp_summary["Critical (<10d)"],
            "Warning (<30d)": app_summary["Warning (<30d)"] + sp_summary["Warning (<30d)"],
            "≤60 days": app_summary["≤60 days"] + sp_summary["≤60 days"],
            "≤90 days": app_summary["≤90 days"] + sp_summary["≤90 days"],
            ">90 days": app_summary[">90 days"] + sp_summary[">90 days"],
            "Unknown": app_summary["Unknown"] + sp_summary["Unknown"],
        },
        "applications": app_rows,
        "servicePrincipals": sp_rows,
        "_title": "App Credentials Expiry Overview",
        "_subtitle": "Secrets and Certificates nearing expiry across Applications & Service Principals",
        "_container_class": "app-creds",
        "_inline_css": APP_CREDS_CSS,
        "_inline_js":  APP_CREDS_JS,
    }

    # Optional: write single-module HTML report if args.html provided
    if getattr(args, "html", None):
        path = args.html if args.html.endswith(".html") else args.html + ".html"
        fncWriteHTMLReport(path, "app_credentials_expiry", data)

    fncPrintMessage("App Credentials Expiry module complete.", "success")
    return data