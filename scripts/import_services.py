"""PyInstaller runtime hook: optional offline import smoke, never opens services."""

import os

if os.environ.get("ICM_IMPORT_SMOKE") == "1":
    from pymobiledevice3.services.afc import AfcService
    from pymobiledevice3.services.diagnostics import DiagnosticsService
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    assert AfcService and DiagnosticsService and InstallationProxyService
