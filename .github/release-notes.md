VISTA ${VERSION}. Each archive is a complete install for one platform: unpack it and run
the launcher. Nothing else needs installing.

## What's changed

<!-- Fill this in before publishing. -->

## Downloads

| platform | archive | sha256 |
|---|---|---|
${ARCHIVE_TABLE}

Built from `${COMMIT}` with build inputs `${INPUTS_COMMIT}` (corpus and vector store).

## Install

Full details, including where state is kept, are in the
[README](${REPO_URL}/blob/${TAG}/README.md#running-a-prebuilt-package).

### macOS (Apple Silicon)

Download from a terminal with `curl`, which leaves no quarantine flag, and start VISTA from
the terminal too. The app is ad-hoc signed rather than signed with a Developer ID, so macOS
blocks a quarantined copy that is double-clicked.

```bash
curl -fLO ${DOWNLOAD_URL}/vista-${VERSION}-mac-arm64.tar.gz
curl -fLO ${DOWNLOAD_URL}/vista-${VERSION}-mac-arm64.tar.gz.sha256
shasum -a 256 -c vista-${VERSION}-mac-arm64.tar.gz.sha256
mkdir -p ~/vista && tar -xf vista-${VERSION}-mac-arm64.tar.gz -C ~/vista
cd ~/vista/vista-${VERSION}-mac-arm64 && ./vista
```

If you downloaded it in a browser, `./vista` clears the flag itself when started from a
terminal. If macOS still refuses to open it, clear the flag by hand and run it again:

```bash
xattr -dr com.apple.quarantine ~/vista/vista-${VERSION}-mac-arm64
```

Or allow it in System Settings → Privacy & Security after the first refusal.

### Linux (x86-64)

```bash
sha256sum -c vista-${VERSION}-linux-x86.tar.gz.sha256
mkdir -p ~/vista && tar -xf vista-${VERSION}-linux-x86.tar.gz -C ~/vista
cd ~/vista/vista-${VERSION}-linux-x86 && ./vista
```

VISTA needs hardware virtualisation through `/dev/kvm`, usually granted by the `kvm` group
(`sudo usermod -aG kvm $USER`, then log in again). It also needs a desktop session. On stock
Ubuntu, the launcher prints two commands that turn on the window's sandbox.

### Windows (x64)

In PowerShell, check the download against its `.sha256` file:

```powershell
(Get-FileHash vista-${VERSION}-win-x86.zip -Algorithm SHA256).Hash.ToLower()
Get-Content vista-${VERSION}-win-x86.zip.sha256
```

Then extract the zip (Explorer's Extract All works) and run `vista.cmd` from the extracted
folder. Keep the folder near the root of a drive, such as `C:\vista`, because Windows limits
path lengths.

## Verified on real hardware

${CHECKLIST}
