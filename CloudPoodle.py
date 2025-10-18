#!/usr/bin/env python3
# ================================================================
# Tool     : CloudPoodle
# Purpose  : Multi-cloud configuration review framework
# Notes    : "Because every cloud deserves a good sniff." 🐩
# ================================================================

import os, argparse, pathlib
from datetime import datetime, timezone

from core.config import fncInitConfig, fncApplyCliOverrides, fncIsDebug
from core.utils import fncPrintMessage, fncSetDebug, fncDisplayBanner, fncBlurb
from core.module_loader import fncRunModule, fncRunAllModules
from core.exports import (
    fncExportList,
    fncExportSingleModule,
    fncExportMultiModule,
)

# ================================================================
# Function: fncParseArguments
# Purpose  : Define and parse command-line arguments for CloudPoodle
# Notes    : Supports multi-cloud providers, run-all, parallel, exports
# ================================================================
def fncParseArguments():
    parser = argparse.ArgumentParser(
        prog="CloudPoodle",
        description="CloudPoodle 🐩 — Multi-cloud misconfiguration sniffer"
    )

    parser.add_argument(
        "provider",
        choices=["entra", "aws", "gcp"],
        help="Specify which cloud provider to target"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scan",
        help="Name of module to execute (e.g., tenant_overview, certs_expiry)"
    )
    group.add_argument(
        "--run-all",
        action="store_true",
        help="Run all available modules for the selected provider"
    )

    parser.add_argument(
        "--skip",
        help="Comma-separated module names to skip with --run-all",
        default=""
    )

    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Number of modules to run concurrently with --run-all (default: 1 = sequential)"
    )

    parser.add_argument(
        "--export",
        nargs="*",
        metavar="FMT[,FMT...]",
        help="Export formats: html, csv, json. Example: --export html,csv json",
        default=None
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug output"
    )

    return parser.parse_args()


# ================================================================
# Function: fncInitClient
# Purpose  : Initialise cloud provider-specific API clients
# Notes    : Always constructs GraphClient; if any credential is missing,
#            we print a friendly notice and let GraphClient prompt.
# ================================================================
def fncInitClient(provider: str, cfg: dict):
    if provider == "entra":
        from handlers.graph.client import GraphClient

        entra_cfg = cfg.get("providers", {}).get("entra", {})
        tenant_id = entra_cfg.get("tenant_id") or os.getenv("CLOUDPOODLE_TENANT_ID")
        client_id = entra_cfg.get("client_id") or os.getenv("CLOUDPOODLE_CLIENT_ID")
        client_secret = entra_cfg.get("client_secret") or os.getenv("CLOUDPOODLE_CLIENT_SECRET")

        if not all([tenant_id, client_id, client_secret]):
            fncPrintMessage("Missing Entra credentials — dropping into interactive mode…", "warn")

        # DO NOT bail early; let GraphClient ask for what’s missing
        return GraphClient(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )

    elif provider == "aws":
        fncPrintMessage("AWS support coming soon — the Poodle is still training.", "warn")
        return None

    elif provider == "gcp":
        fncPrintMessage("GCP support coming soon — the Poodle's fetching biscuits.", "warn")
        return None

    else:
        fncPrintMessage(f"Unsupported provider: {provider}", "error")
        return None


# ================================================================
# Function: main
# Purpose  : Main entry point for CloudPoodle execution
# Notes    : Handles CLI parsing, config loading, client init, module execution
# ================================================================
def main():
    # Parse arguments
    args = fncParseArguments()

    # Load or create configuration, set debug
    cfg = fncInitConfig()
    cfg = fncApplyCliOverrides(cfg, args)
    fncSetDebug(fncIsDebug(cfg))

    # Display startup banner and blurb
    fncDisplayBanner("v1.0")
    fncBlurb(args.provider)
    fncPrintMessage(f"🐩 Unleashing CloudPoodle on {args.provider.upper()}...", "info")
    if cfg.get("debug") or args.debug:
        fncPrintMessage("Debug output enabled.", "debug")

    # Initialise provider-specific client
    client = fncInitClient(args.provider, cfg)
    if not client:
        fncPrintMessage("Unable to continue without valid provider client.", "error")
        return

    # Prepare exports
    export_formats = fncExportList(args.export)
    reports_root = pathlib.Path.home() / ".cloudpoodle" / "reports"

    # Execute modules
    if args.run_all:
        # Warn about aggressive parallelism
        if args.parallel and args.parallel > 4:
            fncPrintMessage("Warning: --parallel > 4 may hit Microsoft Graph throttling.", "warn")

        skip_list = [m.strip() for m in args.skip.split(",") if m.strip()]
        results = fncRunAllModules(args.provider, client, args, skip_list=skip_list)

        # Exports for multi-module runs
        if export_formats:
            fncExportMultiModule(results, export_formats, reports_root)

    else:
        # Single module path
        fncPrintMessage(f"Running scan module: {args.scan}", "info")
        result = fncRunModule(args.provider, args.scan, client, args)

        # Exports for single-module runs
        if export_formats and isinstance(result, dict):
            fncExportSingleModule(args.scan, result, export_formats, reports_root)

    # Wrap up
    fncPrintMessage("Scan complete. Tail wag achieved.", "success")


if __name__ == "__main__":
    main()
