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

This downloads the package for your computer, checks it, installs it and starts VISTA. Run
the same command again to start VISTA later, or to upgrade from an older version; your chats
and settings are kept. After the first install you can also start it with `vista` in a
terminal (macOS and Linux) or from the Start menu (Windows).

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

Pick your platform. Each one is four steps: download, check, unpack, start.

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

3. **Unpack** it into a `vista` folder in your home folder.

   ```bash
   mkdir -p ~/vista && tar -xf vista-${VERSION}-mac-arm64.tar.gz -C ~/vista
   ```

4. **Start** VISTA. The VISTA window opens, and closing it stops VISTA.

   ```bash
   ~/vista/vista-${VERSION}-mac-arm64/vista
   ```

**If macOS says the app can't be opened**, run
`xattr -dr com.apple.quarantine ~/vista/vista-${VERSION}-mac-arm64` and start it again, or
allow it in System Settings → Privacy & Security.

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

3. **Unpack** it into a `vista` folder in your home folder.

   ```bash
   mkdir -p ~/vista && tar -xf vista-${VERSION}-linux-x86.tar.gz -C ~/vista
   ```

4. **Start** VISTA from a desktop session. The VISTA window opens, and closing it stops VISTA.

   ```bash
   ~/vista/vista-${VERSION}-linux-x86/vista
   ```

**If it says it can't use `/dev/kvm`**, run `sudo usermod -aG kvm $USER`, log out and back
in, and start it again. On stock Ubuntu it may also print two commands that turn on the
window's sandbox; run them once.

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

4. **Start** VISTA by double-clicking `vista.cmd` in `C:\vista\vista-${VERSION}-win-x86`, or
   from PowerShell:

   ```powershell
   C:\vista\vista-${VERSION}-win-x86\vista.cmd
   ```

</details>

Where VISTA keeps your data, and how to upgrade, are in the
[README](${REPO_URL}/blob/${TAG}/README.md#running-a-prebuilt-package).

</details>

<details>
<summary>Build and verification</summary>

Built from `${COMMIT}` with build inputs `${INPUTS_COMMIT}` (corpus and vector store).

${CHECKLIST}

</details>
