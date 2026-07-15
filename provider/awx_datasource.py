from collections.abc import Mapping
from typing import Any

import requests

from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from dify_plugin.interfaces.datasource import DatasourceProvider

_TIMEOUT = 15


class AwxDatasourceProvider(DatasourceProvider):
    def _validate_credentials(self, credentials: Mapping[str, Any]) -> None:
        awx_url = (credentials.get("awx_url") or "").rstrip("/")
        token = credentials.get("awx_token") or ""
        ssl_verify = credentials.get("ssl_verify", False)

        if not awx_url:
            raise ToolProviderCredentialValidationError("AWX Base URL is required.")
        if not token:
            raise ToolProviderCredentialValidationError("AWX API Token is required.")

        try:
            resp = requests.get(
                f"{awx_url}/api/v2/me/",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                verify=bool(ssl_verify),
                timeout=_TIMEOUT,
            )
        except requests.exceptions.ConnectionError as e:
            raise ToolProviderCredentialValidationError(
                f"Cannot connect to AWX at '{awx_url}': {e}"
            ) from e
        except requests.exceptions.Timeout:
            raise ToolProviderCredentialValidationError(
                f"Connection to AWX timed out after {_TIMEOUT}s."
            )

        if resp.status_code == 401:
            raise ToolProviderCredentialValidationError(
                "Invalid AWX API Token (HTTP 401 Unauthorized)."
            )
        if resp.status_code == 403:
            raise ToolProviderCredentialValidationError(
                "AWX Token has insufficient permissions (HTTP 403 Forbidden)."
            )
        if not resp.ok:
            raise ToolProviderCredentialValidationError(
                f"AWX returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
