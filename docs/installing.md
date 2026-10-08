# Installing VISTA

How to install a release package, open it, and keep it up to date. To run VISTA from a
checkout instead, see [development.md](development.md); to build a package yourself, see
[building-packages.md](building-packages.md).

## Install

Install and start the newest release with one command. On macOS (Apple Silicon) or Linux
(x86-64), in a terminal:

```bash
curl -fsSL https://github.com/Genesis-VISTA/vista/releases/latest/download/install.sh | bash
```

On Windows (x64), in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://github.com/Genesis-VISTA/vista/releases/latest/download/install.ps1 | iex"
```

It downloads the package for your machine, checks it against its `.sha256`, installs it and
opens VISTA. Pipe it to `bash -s -- --no-launch` instead of `bash` to install only, or add
`--version 0.2.0-rc1` the same way for another release.

**VISTA is an application on every platform.** Open it the way you open any other:

| | Where it is installed | Open it from |
|---|---|---|
| macOS | `/Applications/VISTA`, or `~/Applications/VISTA` when your account cannot write to `/Applications` | Spotlight, Launchpad, Finder's Applications or the Dock: `VISTA.app` |
| Linux | `~/.local/share/vista/app` | the app menu, which runs `app/window/vista-app` |
| Windows | `%LOCALAPPDATA%\VISTA\app` | the Start menu, which runs `app\window\VISTA.exe --startup` |

It shows its own startup in a window straight away, then the VISTA interface; closing it stops
VISTA. Set `VISTA_INSTALL_DIR` to install somewhere else.

The installed folder is the application: keep it together. On macOS, `VISTA.app` must stay
beside `app/`, `bin/`, `node/`, `payload/`, `manifest.json` and `vista`, which are its runtime,
and says so if it is moved out.

### Installing by hand

Follow the steps in the release's notes. On macOS or Linux, in short: download the archive for
your platform and its `.sha256` from the [release](https://github.com/Genesis-VISTA/vista/releases)
with `curl` (a browser download is blocked by macOS), check it, unpack it, and put the folder
where the table above says:

```bash
shasum -a 256 -c vista-<version>-<platform>.tar.gz.sha256
tar -xf vista-<version>-<platform>.tar.gz
mv vista-<version>-mac-arm64 /Applications/VISTA && open /Applications/VISTA/VISTA.app   # macOS
mv vista-<version>-linux-x86 ~/.local/share/vista/app && ~/.local/share/vista/app/app/window/vista-app   # Linux
```

Extract with the platform's own `tar`. macOS `bsdtar` stores extended attributes by default;
GNU `tar` needs `--xattrs`. Those attributes carry the bundled `msb` binary's code signature,
without which the code-execution sandbox cannot create microVMs.

## First launch

First run extracts the corpus, vector store, and embedding weights from the package's
`payload/payload.tar` into the state directory (~1 GB), imports the sandbox image, and seeds the
database. That takes a few minutes. The startup window shows each real activity; later runs mark
already-prepared work and start in seconds. Startup failures offer Retry, Open Logs, Copy
Diagnostics, and Quit without exposing a terminal.

VISTA then opens in its own window. Closing the window, or Cmd-Q on macOS, stops VISTA. If the
window crashes, the services are stopped. If another VISTA window is already open, a second
launch brings the existing startup or main window forward rather than starting another stack.

Paste your inference API key into Settings › Agent (Settings is at the bottom of the sidebar).
AmSC i2 is the provider out of the box; AmSC MAG, OLCF Inference and a custom endpoint are the
other choices there, each with its own key. Settings saves as you go, and a change takes effect
on your next message; no restart. Choose a model from the picker at the top of the chat.

Links to other sites, including the Globus login, open in your default browser; VISTA's own
PDFs open in a second VISTA window (or in your browser, on Linux without the sandbox; see
below), and downloads ask where to save.

To submit HPC jobs, connect each cluster in Settings; see [hpc.md](hpc.md).

## VISTA needs a desktop session

VISTA is a desktop application and has no browser mode. In a session that can't show the
window, it says why and stops before starting anything: an SSH session, no display, or, on
Linux, running as root or missing system libraries (see below). On Linux, where there is no
window to say it in, that is a desktop notification and a line in `logs/window.log`.

**The terminal launchers are for diagnostics.** `vista` (macOS, Linux; also linked as
`~/.local/bin/vista`) and `vista.cmd` (Windows) start the same VISTA from a terminal, printing
each step, and are what to run when something needs looking into. They make the same check and
start the same window, so they too need a desktop session: they are not a way to run VISTA
over SSH. With a terminal launcher, Ctrl-C and closing its terminal stop VISTA too.
`./vista --help` lists the options below.

## Upgrading and state

Run the installer again to upgrade, with VISTA closed (it refuses while VISTA is running). Your
state is kept.

All state lives in the state directory: `vista.db`, uploads, the corpus, the sandbox image
store, and `logs/` (`mcp.log`, `backend.log`, `ui.log`, `window.log`, `setup.log`). The window's
own browser cache is kept apart, in `~/Library/Application Support/VISTA` on macOS,
`~/.config/VISTA` on Linux, and `%APPDATA%\VISTA` on Windows.

The installed package folder is disposable. Upgrading is replacing that folder, which the
installer does, and starting over is deleting the state directory.

| Variable             | Description | Default    |
| -------------------- | ----------- | ---------- |
| `VISTA_HOME`         | State directory. Keep the path under 60 characters. The sandbox derives a Unix socket path from it and the kernel caps that at 104 bytes. Checked at startup. | `~/.vista` |
| `VISTA_UI_PORT`      | Web interface | `3000`     |
| `VISTA_MCP_PORT`     | MCP server | `8000`     |
| `VISTA_BACKEND_PORT` | Backend | `8001`     |
| `VISTA_BACKEND_FORUM__ENABLED` | The Hypothesis Lab. A project's lab also needs its own repository, set in the project's settings, and git 2.34 or later. `false` turns it off everywhere. | `true` |

## Linux

**Hardware virtualisation.** VISTA requires `/dev/kvm`, and the launcher refuses to start
without it. A bare-metal workstation has it; a virtual machine needs nested virtualisation
enabled by its host; and access is usually gated on the `kvm` group, so
`sudo usermod -aG kvm $USER` and a fresh login is the common fix. Without it, the startup window
says so and stops.

**System libraries.** The window needs a desktop session (X11 or Wayland) and four system
libraries that every desktop install already has. A minimal server or a container may not have
them, and then VISTA names what is missing and stops:

| | Debian / Ubuntu | Fedora / RHEL |
|---|---|---|
| GTK 3 | `libgtk-3-0t64` | `gtk3` |
| NSS | `libnss3` | `nss` |
| ALSA | `libasound2t64` | `alsa-lib` |
| GBM | `libgbm1` | `mesa-libgbm` |

```bash
sudo apt install libgtk-3-0t64 libnss3 libasound2t64 libgbm1   # Debian, Ubuntu
sudo dnf install gtk3 nss alsa-lib mesa-libgbm                  # Fedora, RHEL
```

**Chromium's sandbox on Ubuntu.** The window's pages run inside Chromium's sandbox, which needs
unprivileged user namespaces. Ubuntu 23.10 and later allow those only to programs an AppArmor
profile names. So on stock Ubuntu the window starts without the sandbox, and the startup window
says so on every start, with the two commands that turn it on. The package ships the profile.
Installing it is a one-time step that covers every later version:

```bash
sudo install -m 644 ~/.local/share/vista/app/app/window/vista-window.apparmor /etc/apparmor.d/vista-window
sudo apparmor_parser -r /etc/apparmor.d/vista-window
```

VISTA shows these with your package's own path. Debian and Fedora need no step. Where the host
blocks user namespaces some other way, such as inside a container, the window also runs without
the sandbox and says so, with nothing to install. While the sandbox is off, PDFs open in your
default browser instead of a VISTA window, so the browser's own sandbox handles them. Each start
writes `renderer sandbox: on` or `off (--no-sandbox)` to `logs/window.log` in the state
directory.
