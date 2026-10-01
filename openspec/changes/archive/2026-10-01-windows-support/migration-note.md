# Sandbox store migration (microsandbox 0.5.7 → 0.7.2)

Paste into the MR description and the release notes.

## What changes

This release upgrades the code-execution sandbox runtime from microsandbox 0.5.7 to 0.7.2. The first time 0.7.2 opens a sandbox store, it migrates the store in place. Sandbox images are kept, so nothing is rebuilt or re-imported.

| Where | Store |
|---|---|
| Development checkouts | `~/.microsandbox` (or `MSB_HOME`) |
| Packaged installs | `~/.vista/microsandbox` (or `$VISTA_HOME/microsandbox`) |

## The migration is one-way

Once migrated, a store no longer opens under 0.5.7. Any sandbox command then fails with an error like:

```
Migration file of version 'm20260621_000002_create_maintenance_lease' is missing, this migration has been applied but its file is missing
```

In VISTA this shows up as agent code execution failing to start.

## Who needs to do something

- **Developers:** after the first run on this release, every other checkout or worktree still on an older commit fails to start its sandbox. Rebase it onto this release, or reset it (below).
- **Researchers upgrading a package:** nothing to do.
- **Anyone rolling back** to a release older than this one, whether a package or a checkout: reset the store first.

## Reset

Delete the store directory. The older version recreates it and re-imports or rebuilds its sandbox image on its next start, which takes a minute or two.

```bash
rm -rf ~/.microsandbox          # development checkout (or "$MSB_HOME")
rm -rf ~/.vista/microsandbox    # packaged install (or "$VISTA_HOME/microsandbox")
```

This removes only sandbox images and stopped sandboxes. Conversations, uploads, credentials and the knowledge base live elsewhere and are not affected.

Verified 2026-09-23 on macOS against a throwaway store: 0.5.7 imports the image; 0.7.2 migrates the store and runs a sandbox with no re-import; 0.5.7 then fails as shown above; after the reset, 0.5.7 re-imports and runs a sandbox.
