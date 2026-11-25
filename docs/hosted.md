# Running a hosted VISTA

A hosted VISTA is one VISTA serving many researchers from a server, in a browser, behind a
login gateway. This page is for whoever runs it. A researcher's own computer runs the desktop
VISTA ([installing.md](installing.md)); the two are the same code, and the desktop is the
hosted case with one implicit user. How to deploy on a particular machine is outside this
page: it covers what VISTA reads and what it does.

## What hosted is

- **Who is asking.** Every request carries the researcher's access token from your OIDC
  provider (Keycloak). The backend verifies it itself, whatever the gateway already
  checked: signature, issuer, `typ` `Bearer`, an allowed `azp`, and a client role. A user is
  made on their first verified request. A gateway that only forwards a header is not enough.
- **Whose data it is.** Projects, conversations, files, skills, knowledge bases, jobs,
  settings and credentials each belong to one user. Another user's things answer `404`, as if
  they did not exist, and names are unique per user. Sharing stays with the
  [collaboration server](collaboration-server.md)'s teams.
- **Where it runs.** The UI, the backend and the MCP server run side by side on one machine.
  The backend and the MCP server listen on loopback only; the gateway reaches the UI.
- **The same data folder holds everything.** One SQLite database and one folder of files,
  one backend worker. Back up the data folder.

## What it reads

Set these in the backend's environment. Every name below is read from the code
(`backend/src/vista_backend/config.py`); a name not listed is not one VISTA reads.

| Variable | Meaning |
| --- | --- |
| `VISTA_BACKEND_DEPLOYMENT` | `hosted` turns all of this on. The default, `desktop`, is one researcher. |
| `VISTA_BACKEND_ENCRYPTION_KEY` | A Fernet key that encrypts every stored credential. **Required**: a hosted backend refuses to start on the development key or none. Make one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`, keep it outside the data folder (a key stored beside what it encrypts protects nothing), and do not lose it: credentials saved under it cannot be read without it. |
| `VISTA_MCP_SECRET` | A random secret shared by the backend and the MCP server, which refuses any request without it. **Required**, and the same value in both processes. |
| `VISTA_BACKEND_OIDC_ISSUER` | The realm whose access tokens are accepted; its keys are fetched from `{issuer}/protocol/openid-connect/certs`. Default: Obsidian's realm. |
| `VISTA_BACKEND_OIDC_ALLOWED_AZP` | The clients (`azp`) whose tokens count. Default `["apisix"]`. |
| `VISTA_BACKEND_OIDC_REQUIRED_ROLE` | `<client>/<role>` a token must carry. Default `apisix/vista`. |
| `VISTA_BACKEND_SIGN_OUT_URL` | Where the UI's sign-out control goes: your gateway's logout page. Empty: no link. |
| `VISTA_BASE_PATH` | A **build-time** setting of the UI, not the backend: serve it under a path such as `/vista/ui` (default: the root). Set it when building the UI; it cannot change at runtime. |
| `VISTA_BACKEND_URL` | Where the UI's server reaches the backend. Default: the local backend on `:8001`. |
| `VISTA_DATA_DIR` | The data folder (database, sandbox volumes, knowledge bases, secrets). |
| `VISTA_BACKEND_ALLOWED_CLUSTERS` | Comma-separated clusters (`odo`, `frontier`, `lux`, `perlmutter`) this server supports. Unset: Odo alone. See [HPC clusters](hpc.md). |
| `VISTA_BACKEND_SERVER_INFERENCE_URL` | The server's own OpenAI-compatible model endpoint, offered to everyone as the *Server* provider. Default `http://127.0.0.1:8100/v1`. |
| `VISTA_BACKEND_SERVER_INFERENCE_MODEL` | Its model name. Default `default`. |
| `VISTA_BACKEND_SERVER_INFERENCE_KEY` | Its key, if it wants one. |
| `VISTA_BACKEND_SANDBOXES_PER_USER` | Most agent sandboxes one user may have at once. Default 4. |
| `VISTA_BACKEND_SANDBOXES_TOTAL` | Most in all. Default 40. |
| `VISTA_BACKEND_SKILL_IMPORT_ALLOWED_HOSTS` | Comma-separated hosts a skill may be imported from (https only). Empty allows none. |
| `VISTA_BACKEND_COLLABORATION_SERVER_URL`, `VISTA_BACKEND_COLLABORATION_ENABLED` | The collaboration server, and whether it is used at all. |
| `VISTA_BACKEND_SECRETS__DIRECTORY` | The secrets directory (default `<data dir>/secrets`), which holds Slack's two tokens as one file each. It must be mode `0700`. An operator may mount a secret as a file of that name. |

These server settings are the environment's alone: a researcher's Settings page shows them
read-only, and a save that includes them leaves them as they are.

### Slack

One Slack app serves the whole server ([VISTA in Slack](slack.md)). Its two tokens are files
in the secrets directory; its limits are set here, and a researcher cannot change them. Unset,
a value stays what the app's saved settings say.

| Variable | Meaning |
| --- | --- |
| `VISTA_BACKEND_SLACK__HOSTED_ENABLED` | Turns Slack on for everyone. |
| `VISTA_BACKEND_SLACK__HOSTED_ALLOWED_TEAM_IDS`, `…_ALLOWED_CHANNEL_IDS` | Which workspaces and channels may use it (a JSON list). |
| `VISTA_BACKEND_SLACK__HOSTED_MAX_NODES`, `…_MAX_WALLTIME_MINUTES`, `…_MAX_JOBS_PER_DAY` | The limits on jobs submitted from Slack. |

Each researcher links their own Slack account in their Settings, and Slack then acts as that
researcher: their projects and their credentials. The lock (block every action that changes
something) is each researcher's own.

## What researchers get, and what they do not

They get their own projects, chats, files, skills, knowledge bases and settings; the
server's model as an inference provider, and their own MAG or OLCF key if they have one;
Odo (or the clusters you allow), with their own credentials; and collaboration signed in
with their gateway login, with nothing to paste.

Hosted turns these off:

- the **Custom** inference provider and the NERSC cluster;
- every cluster outside `VISTA_BACKEND_ALLOWED_CLUSTERS`, and one Globus connection shared by
  every cluster (each researcher connects their own; Odo's S3M token is checked to be for an
  open enclave);
- **updates** in the app (you update the server) and the desktop window's own actions;
- **linked folders** (a researcher's own folders cannot be linked to a server) and skill
  import from hosts you have not allowed;
- the **saved collaboration token** (the gateway login is used instead);
- calling an MCP tool by hand (`/projects/{name}/mcp/call`).

Sandboxes are per user: a user's files live under `volumes/<user>/<project>`, and the agent's
commands run with a scrubbed environment, not the backend's.

## Known follow-ups

- **The Hypothesis Lab is off.** Its debates run in the background, where there is no
  signed-in request to post to a forum with. Hosted forums need a service token for the
  collaboration server.
- **Collaboration in the background.** Anything that runs with no request (a job watch, a
  Slack turn) has no gateway token, so collaboration reports that it needs the web app.
- **Frontier, Lux and NERSC** are not supported hosted: their credential storage is not
  settled.
- **No admin UI**, no sharing inside VISTA beyond the collaboration server's teams, and one
  backend worker on SQLite, which suits a handful of researchers.

## Trying it on one machine

`./launch.sh logs --no-electron --hosted` from a checkout starts the whole stack hosted, behind a
development sign-in on `http://127.0.0.1:3100` that stands in for the gateway: type a name,
and that is a user. Open it in two browsers as `alice` and `bob` to see them separated. It is
for development only and is not in any package.
