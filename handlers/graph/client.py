# ================================================================
# File     : client.py
# Purpose  : Microsoft Graph API read-only client for Entra (Azure AD)
# Notes    : Read-only: GET + pagination + retries. No destructive ops.
#            - Auto-refresh token on 401
#            - Proactive refresh if token expires in <5 minutes
# ================================================================

import os
import msal
import requests
import time
import getpass
from typing import Dict, Any, List, Optional
from core.utils import fncPrintMessage, fncRetry

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

class GraphClient:
    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        # Try environment variables first
        tenant_id = tenant_id or os.getenv("CLOUDPOODLE_TENANT_ID")
        client_id = client_id or os.getenv("CLOUDPOODLE_CLIENT_ID")
        client_secret = client_secret or os.getenv("CLOUDPOODLE_CLIENT_SECRET")

        # Prompt interactively if any credential is missing
        if not tenant_id:
            tenant_id = input("Enter Tenant ID: ").strip()
        if not client_id:
            client_id = input("Enter Application (Client) ID: ").strip()
        if not client_secret:
            fncPrintMessage(
                "No Client Secret found *Hidden* "
                "Credentials are stored in environment only for this session.",
                "warn",
            )
            client_secret = getpass.getpass("Enter Client Secret (input hidden): ").strip()

        # Persist to environment for the lifetime of the session
        os.environ["CLOUDPOODLE_TENANT_ID"] = tenant_id
        os.environ["CLOUDPOODLE_CLIENT_ID"] = client_id
        os.environ["CLOUDPOODLE_CLIENT_SECRET"] = client_secret

        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret

        # Application scope (app-only). Ensure the app has appropriate read-only app perms.
        self.scope = ["https://graph.microsoft.com/.default"]
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"

        fncPrintMessage("Initialising Microsoft Graph (read-only) client...", "info")

        # MSAL ConfidentialClientApplication
        self.app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
        )

        # token/bookkeeping
        self.token: str = ""
        self._token_expires_on: int = 0  # epoch seconds
        self._set_token(self._acquire_token())

        fncPrintMessage("GraphClient initialised (read-only).", "success")

    # ---------- Token helpers ----------

    def _acquire_token(self) -> Dict[str, Any]:
        """Acquire a token using MSAL (silent -> client creds). Returns MSAL result dict."""
        fncPrintMessage("Requesting Microsoft Graph access token...", "debug")
        result = self.app.acquire_token_silent(self.scope, account=None)
        if not result:
            result = self.app.acquire_token_for_client(scopes=self.scope)
        if "access_token" not in result:
            fncPrintMessage(
                f"MSAL Authentication failed: {result.get('error_description', 'Unknown error')}",
                "error",
            )
            raise Exception("Failed to acquire access token")
        return result

    def _set_token(self, msal_result: Dict[str, Any]) -> None:
        """Store token and expiry from MSAL result."""
        self.token = msal_result["access_token"]
        try:
            self._token_expires_on = int(msal_result.get("expires_on") or 0)
        except Exception:
            self._token_expires_on = 0
        if not self._token_expires_on:
            self._token_expires_on = int(time.time()) + int(msal_result.get("expires_in", 3600))

    def _ensure_fresh_token(self) -> None:
        """Proactively refresh token if it expires in <5 minutes."""
        now = int(time.time())
        if now >= (self._token_expires_on - 300):  # <5 minutes remaining
            fncPrintMessage("Refreshing access token (nearing expiry)...", "debug")
            self._set_token(self._acquire_token())

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ---------- HTTP handling ----------

    def _handle_response(self, response: requests.Response) -> Dict[str, Any]:
        status = response.status_code

        # Success
        if status == 200:
            return response.json()

        # Rate limit
        if status == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            fncPrintMessage(f"Rate limit hit. Sleeping for {retry_after}s...", "warn")
            time.sleep(retry_after)
            req = response.request
            resp = requests.request(method=req.method, url=req.url, headers=self._auth_headers(), data=req.body)
            return self._handle_response(resp)

        # Unauthorized (refresh and retry once)
        if status == 401:
            try:
                body = response.json()
            except Exception:
                body = {}
            err = (body.get("error") or {})
            code = err.get("code") or ""
            msg = err.get("message") or ""
            if "InvalidAuthenticationToken" in code or "expired" in str(msg).lower():
                fncPrintMessage("Access token expired, Attempting Refresh.", "warn")
                self._set_token(self._acquire_token())
                req = response.request
                resp = requests.request(method=req.method, url=req.url, headers=self._auth_headers(), data=req.body)
                if resp.status_code == 200:
                    return resp.json()
                # fall through to generic error handling below if still failing
            fncPrintMessage(f"Unauthorized (401): {response.text}", "error")
            raise Exception("Graph API request failed with status 401")

        # Other client/server errors
        if status >= 400:
            fncPrintMessage(f"Graph API Error [{status}] -> {response.text}", "error")
            raise Exception(f"Graph API request failed with status {status}")

        # Fallback
        try:
            return response.json()
        except Exception:
            return {"status": status, "text": response.text}

    def _request(self, method: str, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Single HTTP request with proactive token refresh and 401 auto-refresh retry."""
        self._ensure_fresh_token()
        resp = requests.request(method, url, headers=self._auth_headers(), params=params)
        return self._handle_response(resp)

    # ---------- Public API ----------

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Perform a GET request to a Graph endpoint (single page).
        Use get_all for paginated resources.
        """
        url = f"{GRAPH_ROOT}/{endpoint.strip().lstrip('/')}"
        fncPrintMessage(f"GET {url}", "debug")
        return fncRetry(lambda: self._request("GET", url, params=params))

    def get_all(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Retrieve all items from a paginated Graph endpoint.
        Returns a flat list of items (value) for list endpoints.
        Example: client.get_all("applications?$select=id,displayName")
        """
        url = f"{GRAPH_ROOT}/{endpoint.strip().lstrip('/')}"
        fncPrintMessage(f"GET (all pages) {url}", "debug")

        def _single_page(req_url):
            return self._request("GET", req_url, params=params)

        data = fncRetry(lambda: _single_page(url))
        items: List[Dict[str, Any]] = []

        if isinstance(data, dict) and "value" not in data:
            return [data]

        if isinstance(data, dict) and "value" in data:
            items.extend(data.get("value", []))
            next_link = data.get("@odata.nextLink")
        else:
            return items

        while next_link:
            fncPrintMessage(f"Following nextLink -> {next_link}", "debug")

            page = fncRetry(lambda: self._request("GET", next_link))
            if isinstance(page, dict):
                items.extend(page.get("value", []))
                next_link = page.get("@odata.nextLink")
            else:
                break

        return items
