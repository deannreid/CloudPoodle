# ================================================================
# File     : module_loader.py
# Purpose  : Dynamically load and execute scanning modules
# Notes    : Works across providers (entra, aws, gcp). Provides
#           discovery and run-all support.
# ================================================================

import importlib
import pathlib
import traceback
from typing import Dict, List, Any
from core.utils import fncPrintMessage
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================================================================
# Function: fncLoadModule
# Purpose : Dynamically import a module based on provider and name
# Notes   : Returns the imported module or None if not found
# ================================================================
def fncLoadModule(provider: str, module_name: str):
    try:
        mod_path = f"modules.{provider}.{module_name}"
        mod = importlib.import_module(mod_path)
        fncPrintMessage(f"Loaded module: {mod_path}", "debug")
        return mod
    except ModuleNotFoundError:
        fncPrintMessage(f"Module not found: {provider}/{module_name}", "error")
        return None
    except Exception as ex:
        fncPrintMessage(f"Failed to import {provider}/{module_name}: {ex}", "error")
        return None


# ================================================================
# Function: fncRunModule
# Purpose : Execute a loaded module’s main 'run' function
# Notes   : Expects each module to define a 'run(client, args)' function
# ================================================================
def fncRunModule(provider: str, module_name: str, client, args) -> Any:
    mod = fncLoadModule(provider, module_name)
    if mod and hasattr(mod, "run"):
        try:
            fncPrintMessage(f"Starting module: {provider}/{module_name}", "info")
            result = mod.run(client, args)
            fncPrintMessage(f"Module complete: {provider}/{module_name}", "success")
            return result
        except Exception as ex:
            fncPrintMessage(f"Module {module_name} raised an exception: {ex}", "error")
            fncPrintMessage(traceback.format_exc(), "debug")
            return {"error": str(ex)}
    else:
        fncPrintMessage(f"Module {module_name} missing 'run' function.", "warn")
        return None


# ================================================================
# Function: fncDiscoverModules
# Purpose : Discover available modules for a provider by scanning the modules dir
# Notes   : Ignores __init__.py and files starting with '_' by convention
# ================================================================
def fncDiscoverModules(provider: str) -> List[str]:
    base = pathlib.Path("modules") / provider
    if not base.exists() or not base.is_dir():
        fncPrintMessage(f"No modules directory for provider '{provider}' (expected: {base})", "warn")
        return []

    mods = []
    for p in sorted(base.iterdir()):
        if p.is_file() and p.suffix == ".py" and not p.name.startswith("_") and p.name != "__init__.py":
            mods.append(p.stem)
    fncPrintMessage(f"Discovered modules for {provider}: {mods}", "debug")
    return mods


# ================================================================
# Function: fncRunAllModules
# Purpose : Run every discovered module for a provider in sequence
# Notes   : Returns a dictionary summary { module_name: result_or_error }
#           Skips modules listed in skip_list. Runs modules serially to avoid rate-limits.
# ================================================================
def fncRunAllModules(provider: str, client, args, skip_list: List[str] = None) -> Dict[str, Any]:
    skip_list = skip_list or []
    results = {}
    modules = fncDiscoverModules(provider)

    if not modules:
        fncPrintMessage(f"No modules to run for provider '{provider}'", "warn")
        return results

    fncPrintMessage(f"Running all modules for {provider} (count={len(modules)})", "info")

    for mod_name in modules:
        if mod_name in skip_list:
            fncPrintMessage(f"Skipping module (skip-list): {mod_name}", "debug")
            results[mod_name] = {"skipped": True}
            continue

        try:
            res = fncRunModule(provider, mod_name, client, args)
            results[mod_name] = res
        except Exception as ex:
            # Shouldn't happen because fncRunModule wraps exceptions, but be safe
            fncPrintMessage(f"Unexpected error running {mod_name}: {ex}", "error")
            results[mod_name] = {"error": str(ex)}

    fncPrintMessage("Completed running all modules.", "success")
    return results


def fncRunAllModules(provider: str, client, args, skip_list=None):
    skip_list = skip_list or []
    results = {}
    modules = fncDiscoverModules(provider)
    threads = getattr(args, "parallel", 1)

    fncPrintMessage(f"Running {len(modules)} modules (parallel={threads})", "info")

    if threads <= 1:
        # Sequential mode (safe default)
        for mod in modules:
            if mod in skip_list:
                fncPrintMessage(f"Skipping {mod}", "debug")
                results[mod] = {"skipped": True}
                continue
            results[mod] = fncRunModule(provider, mod, client, args)
    else:
        # Concurrent mode
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = {executor.submit(fncRunModule, provider, mod, client, args): mod for mod in modules if mod not in skip_list}
            for future in as_completed(futures):
                mod_name = futures[future]
                try:
                    results[mod_name] = future.result()
                except Exception as ex:
                    fncPrintMessage(f"Module {mod_name} failed in parallel mode: {ex}", "error")
                    results[mod_name] = {"error": str(ex)}

    fncPrintMessage("All modules completed.", "success")
    return results