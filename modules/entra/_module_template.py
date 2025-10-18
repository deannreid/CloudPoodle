# ================================================================
# File     : modules/entra/<module_name>.py
# Purpose  : <Short description of module purpose>
# Notes    : Follow the run(client, args) signature
# ================================================================

from core.utils import fncPrintMessage, fncToTable, fncExportCSV, fncWriteJSON, fncNewRunId
from core.config import fncGetProviderConfig

REQUIRED_PERMS = ["Directory.Read.All"]  # update per module


# ================================================================
# Function: add_args
# Purpose : Add module CLI arguments (optional)
# Notes   : Called by module loader when building argparse
# ================================================================
def add_args(subparsers):
    p = subparsers.add_parser("example_module", help="Describe module")
    p.add_argument("--export", help="Export CSV to path", default=None)
    return p


# ================================================================
# Function: run
# Purpose : Entry point for module execution
# Notes   : client is an initialised GraphClient
# ================================================================
def run(client, args):
    run_id = fncNewRunId("poodle")
    fncPrintMessage(f"Running example_module (run={run_id})", "info")

    # Example: fetch all applications (customise per real module)
    apps = client.get_all("applications?$select=id,displayName,appId,createdDateTime")

    rows = []
    for a in apps:
        rows.append({
            "id": a.get("id"),
            "displayName": a.get("displayName"),
            "appId": a.get("appId"),
            "created": a.get("createdDateTime")
        })

    # Show table
    print(fncToTable(rows, headers=["displayName", "appId", "created"], max_rows=50))

    # Optional exports
    if getattr(args, "export", None):
        fncExportCSV(args.export, rows)
        fncWriteJSON(args.export + ".json", {"run": run_id, "rows": rows})

    fncPrintMessage(f"example_module complete — {len(rows)} items", "success")
    return rows
