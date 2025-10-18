# ================================================================
# File     : config.py
# Purpose  : Configuration management for CloudPoodle
# Notes    : Handles initial creation, loading, and saving of config
# ================================================================

import os
import json
import pathlib
from core.utils import fncPrintMessage, fncEnsureFolder, fncReadJSON, fncWriteJSON, fncLoadEnv


# ================================================================
# Function: fncDefaultConfig
# Purpose : Return a default configuration dictionary
# Notes   : Called when config file does not exist
# ================================================================
def fncDefaultConfig() -> dict:
    return {
        "version": "1.0",
        "created_by": "Dean",
        "cloudpoodle_home": str(pathlib.Path.home() / ".cloudpoodle"),
        "last_run_id": None,
        "debug": False,
        "providers": {
            "entra": {
                "tenant_id": "",
                "client_id": "",
                "client_secret": "",
                "authority": "https://login.microsoftonline.com"
            },
            "aws": {
                "access_key": "",
                "secret_key": "",
                "region": "eu-west-1"
            },
            "gcp": {
                "service_account_file": "",
                "project_id": ""
            }
        }
    }


# ================================================================
# Function: fncInitConfig
# Purpose : Create or load configuration file
# Notes   : Ensures base folder exists; returns full config dict
# ================================================================
def fncInitConfig(config_path: str = None) -> dict:
    default_path = pathlib.Path.home() / ".cloudpoodle" / "config.json"
    path = pathlib.Path(config_path or default_path)

    fncEnsureFolder(path.parent)

    if not path.exists():
        fncPrintMessage(f"No config found at {path}. Creating default...", "warn")
        cfg = fncDefaultConfig()
        fncWriteJSON(str(path), cfg)
        return cfg
    else:
        return fncLoadConfig(str(path))


# ================================================================
# Function: fncLoadConfig
# Purpose : Load configuration file and apply environment overrides
# Notes   : Uses ENV vars: ENTRA_TENANT_ID, ENTRA_CLIENT_ID, etc.
# ================================================================
def fncLoadConfig(config_path: str) -> dict:
    cfg = fncReadJSON(config_path)

    # Environment overrides (useful in CI/CD or container)
    env_overrides = {
        "entra": {
            "tenant_id": fncLoadEnv("ENTRA_TENANT_ID", cfg["providers"]["entra"].get("tenant_id")),
            "client_id": fncLoadEnv("ENTRA_CLIENT_ID", cfg["providers"]["entra"].get("client_id")),
            "client_secret": fncLoadEnv("ENTRA_CLIENT_SECRET", cfg["providers"]["entra"].get("client_secret")),
        },
        "aws": {
            "access_key": fncLoadEnv("AWS_ACCESS_KEY", cfg["providers"]["aws"].get("access_key")),
            "secret_key": fncLoadEnv("AWS_SECRET_KEY", cfg["providers"]["aws"].get("secret_key")),
            "region": fncLoadEnv("AWS_REGION", cfg["providers"]["aws"].get("region")),
        },
        "gcp": {
            "service_account_file": fncLoadEnv("GCP_SERVICE_ACCOUNT", cfg["providers"]["gcp"].get("service_account_file")),
            "project_id": fncLoadEnv("GCP_PROJECT_ID", cfg["providers"]["gcp"].get("project_id")),
        }
    }

    for provider, values in env_overrides.items():
        cfg["providers"][provider].update(values)

    fncPrintMessage(f"Loaded configuration from {config_path}", "debug")
    return cfg


# ================================================================
# Function: fncSaveConfig
# Purpose : Save configuration file safely
# Notes   : Used to persist changes made during runtime
# ================================================================
def fncSaveConfig(cfg: dict, config_path: str = None) -> None:
    default_path = pathlib.Path.home() / ".cloudpoodle" / "config.json"
    path = pathlib.Path(config_path or default_path)

    fncEnsureFolder(path.parent)
    fncWriteJSON(str(path), cfg)
    fncPrintMessage(f"Configuration saved → {path}", "success")


# ================================================================
# Function: fncUpdateConfigField
# Purpose : Update a specific nested key in the config
# Notes   : Example: fncUpdateConfigField(cfg, "providers.entra.client_id", "abc123")
# ================================================================
def fncUpdateConfigField(cfg: dict, path: str, value) -> dict:
    parts = path.split(".")
    ref = cfg
    for key in parts[:-1]:
        ref = ref.setdefault(key, {})
    ref[parts[-1]] = value
    fncPrintMessage(f"Updated config field: {path} = {value}", "debug")
    return cfg


# ================================================================
# Function: fncGetProviderConfig
# Purpose : Return config block for a specific provider
# Notes   : Provider options: entra, aws, gcp
# ================================================================
def fncGetProviderConfig(cfg: dict, provider: str) -> dict:
    providers = cfg.get("providers", {})
    if provider not in providers:
        fncPrintMessage(f"Provider not found in config: {provider}", "warn")
        return {}
    return providers[provider]


# ================================================================
# Function: fncApplyCliOverrides
# Purpose : Apply command-line flags to the loaded config
# Notes   : Currently handles --debug; extend as needed later
# ================================================================
def fncApplyCliOverrides(cfg: dict, args) -> dict:
    if getattr(args, "debug", None) is not None:
        cfg["debug"] = bool(args.debug)
    return cfg


# ================================================================
# Function: fncIsDebug
# Purpose : Return whether debug mode is enabled in config
# Notes   : Convenience helper for modules
# ================================================================
def fncIsDebug(cfg: dict) -> bool:
    return bool(cfg.get("debug", False))