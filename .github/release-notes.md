VISTA: Visual Intelligence for Scientific & Tooling Assistant.

## Install

**macOS (Apple Silicon) and Linux (x86-64)**: in a terminal:

```bash
curl -fsSL ${DOWNLOAD_URL}/install.sh | bash
```

**Windows (x64)**: in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm ${DOWNLOAD_URL}/install.ps1 | iex"
```

This downloads the package for your computer, checks it, installs it and opens VISTA. After
that, open VISTA like any other application: from Spotlight, Launchpad or Finder's
Applications on macOS, from the app menu on Linux, or from the Start menu on Windows. Run the
same command again to upgrade from an older version, with VISTA closed; your chats and settings
are kept.

VISTA installs into `/Applications/VISTA` on a Mac where your account can write there
(administrators can), and into `~/Applications/VISTA` otherwise; into `~/.local/share/vista/app`
on Linux; and into `%LOCALAPPDATA%\VISTA\app` on Windows.

On Linux, VISTA needs a desktop session and access to `/dev/kvm`. The installer checks both
before downloading and says what to do if either is missing.

## What's changed

<!-- Fill this in before publishing. -->

## Downloads

| platform | archive | sha256 |
|---|---|---|
${ARCHIVE_TABLE}

<details>
<summary>Installing by hand, without the script</summary>

Pick your platform. Each one is four steps: download, check, unpack, open. Keep the unpacked
folder together: the application and everything it runs are in it. To upgrade by hand, close VISTA and
delete the old folder before unpacking the new one; your chats and settings are kept elsewhere.

<details>
<summary>macOS (Apple Silicon)</summary>

1. **Download** the package and its checksum in a terminal. Use `curl`, not a browser: macOS
   blocks a browser-downloaded copy of this app.

   ```bash
   curl -fLO ${DOWNLOAD_URL}/vista-${VERSION}-mac-arm64.tar.gz
   curl -fLO ${DOWNLOAD_URL}/vista-${VERSION}-mac-arm64.tar.gz.sha256
   ```

2. **Check** the download. It should print `OK`.

   ```bash
   shasum -a 256 -c vista-${VERSION}-mac-arm64.tar.gz.sha256
   ```

3. **Unpack** it into `/Applications/VISTA`. Without administrator rights, use
   `~/Applications/VISTA` instead, in both lines.

   ```bash
   tar -xf vista-${VERSION}-mac-arm64.tar.gz
   mv vista-${VERSION}-mac-arm64 /Applications/VISTA
   ```

4. **Open** `VISTA.app` in that folder, from Finder, Spotlight or Launchpad. It shows its startup
   in its own window, then VISTA; quitting it stops VISTA.

   ```bash
   open /Applications/VISTA/VISTA.app
   ```

**If macOS says the app can't be opened**, the package was downloaded with a browser. Run
`xattr -dr com.apple.quarantine /Applications/VISTA` and open it again, or allow it in System
Settings → Privacy & Security.

For diagnostics, `/Applications/VISTA/vista` starts the same VISTA from a terminal.

</details>

<details>
<summary>Linux (x86-64)</summary>

1. **Download** the package and its checksum.

   ```bash
   curl -fLO ${DOWNLOAD_URL}/vista-${VERSION}-linux-x86.tar.gz
   curl -fLO ${DOWNLOAD_URL}/vista-${VERSION}-linux-x86.tar.gz.sha256
   ```

2. **Check** the download. It should print `OK`.

   ```bash
   sha256sum -c vista-${VERSION}-linux-x86.tar.gz.sha256
   ```

3. **Unpack** it into `~/.local/share/vista/app`.

   ```bash
   tar -xf vista-${VERSION}-linux-x86.tar.gz
   mkdir -p ~/.local/share/vista && mv vista-${VERSION}-linux-x86 ~/.local/share/vista/app
   ```

4. **Open** VISTA from a desktop session. It shows its startup in its own window, then VISTA;
   closing it stops VISTA. The one-line installer also adds it to your app menu.

   ```bash
   ~/.local/share/vista/app/app/window/vista-app
   ```

**If it says it can't use `/dev/kvm`**, run `sudo usermod -aG kvm $USER`, log out and back
in, and open it again. On stock Ubuntu its startup window may also show two commands that turn
on the window's sandbox; run them once.

For diagnostics, `~/.local/share/vista/app/vista` starts the same VISTA from a terminal.

</details>

<details>
<summary>Windows (x64)</summary>

1. **Download** `vista-${VERSION}-win-x86.zip` and `vista-${VERSION}-win-x86.zip.sha256` from
   the assets below, into your Downloads folder.

2. **Check** the download in PowerShell. It should print `True`.

   ```powershell
   cd ~\Downloads
   (Get-FileHash vista-${VERSION}-win-x86.zip -Algorithm SHA256).Hash.ToLower() -eq (Get-Content vista-${VERSION}-win-x86.zip.sha256).Split(' ')[0]
   ```

3. **Unpack** it into `C:\vista`. Keep the folder this short, because Windows limits how long
   paths can be.

   ```powershell
   mkdir C:\vista -Force; tar -xf vista-${VERSION}-win-x86.zip -C C:\vista
   ```

4. **Open** VISTA by double-clicking `VISTA.exe` in `C:\vista\vista-${VERSION}-win-x86\app\window`,
   or from PowerShell. It shows its startup in its own window, then VISTA; closing it stops
   VISTA. The one-line installer also adds it to the Start menu.

   ```powershell
   & C:\vista\vista-${VERSION}-win-x86\app\window\VISTA.exe --startup
   ```

   For diagnostics, `vista.cmd` in the same folder starts VISTA from a console.

</details>

Where VISTA keeps your data, and how to upgrade, are in the
[README](${REPO_URL}/blob/${TAG}/README.md#running-a-prebuilt-package).

</details>

<details>
<summary>Build and verification</summary>

Built from `${COMMIT}` with build inputs `${INPUTS_COMMIT}` (corpus and vector store).

${CHECKLIST}

</details>
