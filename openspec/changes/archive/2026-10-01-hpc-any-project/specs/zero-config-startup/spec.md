## REMOVED Requirements

### Requirement: A deployment credential is a fallback, not a requirement

**Reason**: Only a researcher's own Globus tokens authorize Odo and Frontier file operations, so the facility decides what each researcher may read and write. The deployment-wide fallback (`VISTA_MCP_{ODO,FRONTIER}_GLOBUS_*REFRESH_TOKEN`) is removed. Starting without any file-transfer credential is still covered by "Optional file-transfer setup never blocks startup".

**Migration**: On a hosted deployment that relied on the shared Globus login, each researcher connects Globus for each cluster in Settings. Until they do, Odo and Frontier file operations report that file transfer is not connected.
