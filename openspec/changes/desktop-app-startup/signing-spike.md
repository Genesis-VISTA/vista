# macOS signing spike

Date: 2026-10-02

This records task 1.1's baseline against the completed package at
`/Users/f7b/Downloads/vista-0.1.0-mac-arm64`. The package is version
`0.1.0+4053708`, targets macOS arm64, and occupies 4.0 GB after extraction.

## Host and tooling

- macOS 26.7.1, arm64
- Xcode developer directory: `/Applications/Xcode.app/Contents/Developer`
- `notarytool` 1.1.3
- `stapler` present
- Valid code-signing identities: **0**

Developer ID signing and submission cannot be tested on this host until a valid
`Developer ID Application` identity is installed in the login Keychain. No
notarization submission was attempted.

## Executable inventory

The package contains 97,388 regular files. Candidate executable code was found
by taking every executable regular file plus every `.so`, `.dylib`, and `.node`
file, then retaining files identified as Mach-O by `/usr/bin/file`.

| Area | Mach-O code objects |
| --- | ---: |
| `app/mcp_servers` | 300 |
| `app/backend` | 272 |
| `app/window` | 13 |
| bundled Python | 10 |
| `app/ui` | 2 |
| bundled Node | 1 |
| bundled `uv` | 1 |
| **Total** | **599** |

There are another 341 executable non-Mach-O files: 176 shell scripts, 119
Python scripts, 18 other text executables, and 28 other executable files. These
remain part of the distribution inventory even though the Mach-O objects are the
immediate code-signing surface.

The 13 code objects in `VISTA.app` are the main executable, four helper apps,
Electron Framework, three support frameworks, `chrome_crashpad_handler`, and
two Electron dynamic libraries. The current deep verification of that bundle
passes.

## Signature baseline

Strict `codesign` verification of all 599 Mach-O objects produced:

| Result | Signature class | Count |
| --- | --- | ---: |
| valid | ad hoc | 562 |
| invalid | ad hoc | 36 |
| invalid | identified | 1 |

The invalid identified object is `node/bin/node`. It retains team identifier
`HX7739G8FX` and hardened-runtime metadata, but its signature no longer verifies
after packaging. The other 36 failures are native Python extensions distributed
across the backend and MCP environments:

```text
app/backend/.venv/lib/python3.14/site-packages/81d243bd2c585b0f4821__mypyc.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/bcrypt/_bcrypt.abi3.so
app/backend/.venv/lib/python3.14/site-packages/caio/thread_aio.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/charset_normalizer/cd.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/charset_normalizer/md.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/cryptography/hazmat/bindings/_rust.abi3.so
app/backend/.venv/lib/python3.14/site-packages/fastavro/_logical_readers.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/fastavro/_logical_writers.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/fastavro/_read.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/fastavro/_schema.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/fastavro/_validation.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/fastavro/_write.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/google/_upb/_message.abi3.so
app/backend/.venv/lib/python3.14/site-packages/greenlet/_greenlet.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/greenlet/tests/_test_extension.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/greenlet/tests/_test_extension_cpp.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/grpc/_cython/cygrpc.cpython-314-darwin.so
app/backend/.venv/lib/python3.14/site-packages/uvloop/loop.cpython-314-darwin.so
app/mcp_servers/dev_mcp_server/.venv/lib/python3.14/site-packages/caio/thread_aio.cpython-314-darwin.so
app/mcp_servers/dev_mcp_server/.venv/lib/python3.14/site-packages/cryptography/hazmat/bindings/_rust.abi3.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/81d243bd2c585b0f4821__mypyc.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/bcrypt/_bcrypt.abi3.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/caio/thread_aio.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/charset_normalizer/cd.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/charset_normalizer/md.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/cryptography/hazmat/bindings/_rust.abi3.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/fontTools/cu2qu/cu2qu.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/fontTools/feaLib/lexer.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/fontTools/misc/bezierTools.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/fontTools/pens/momentsPen.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/fontTools/qu2cu/qu2cu.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/fontTools/varLib/iup.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/google/_upb/_message.abi3.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/grpc/_cython/cygrpc.cpython-314-darwin.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/nacl/_sodium.abi3.so
app/mcp_servers/vista_mcp_server/.venv/lib/python3.14/site-packages/uvloop/loop.cpython-314-darwin.so
```

This means a production notarization design must account for all packaged
Mach-O code, not only re-sign the Electron application.

## VISTA application baseline

`app/window/VISTA.app` is valid under deep strict verification but is ad-hoc
signed:

- identifier: `gov.ornl.vista`
- CDHash: `96e495121ab7c72da071ac2d76e160be6f40b5b5`
- team identifier: not set
- entitlements: none
- Gatekeeper assessment: rejected with an internal code-signing error
- quarantine attribute: absent, because the existing terminal launcher removed
  it from the package

## Sandbox runtime baseline

The bundled `msb` path is:

```text
app/mcp_servers/dev_mcp_server/.venv/lib/python3.14/site-packages/microsandbox/_bundled/bin/msb
```

Its baseline is:

- version reported by `msb doctor`: 0.7.2
- signature: valid ad hoc
- CDHash: `a918bf783aebc92ef917e8f723c070b3ed0d21a7`
- team identifier: not set
- `com.apple.security.hypervisor = true`
- `com.apple.security.cs.disable-library-validation = true`
- `msb doctor`: host setup ready

Those are the complete entitlements found across the package: `msb` is the only
Mach-O object with a non-empty entitlement dictionary. Task 1.2 must preserve
both keys and prove a real sandbox spawn after any signing change.

## Reproduction commands

```bash
PACKAGE=/Users/f7b/Downloads/vista-0.1.0-mac-arm64

find "$PACKAGE" -type f \
  \( -perm -111 -o -name '*.so' -o -name '*.dylib' -o -name '*.node' \) \
  -print0 | xargs -0 file

codesign --verify --deep --strict --verbose=4 \
  "$PACKAGE/app/window/VISTA.app"

codesign -dvvv --verbose=4 \
  "$PACKAGE/app/mcp_servers/dev_mcp_server/.venv/lib/python3.14/site-packages/microsandbox/_bundled/bin/msb"

codesign -d --entitlements :- \
  "$PACKAGE/app/mcp_servers/dev_mcp_server/.venv/lib/python3.14/site-packages/microsandbox/_bundled/bin/msb"

security find-identity -v -p codesigning
```

## Task 1.2 prerequisite

Install a valid `Developer ID Application` certificate and private key in this
Mac's login Keychain. After that, task 1.2 can copy the package, establish an
explicit signing order for all 599 Mach-O objects, preserve `msb`'s entitlements,
sign the top-level app and submit a distribution container to Apple's notary
service. Notary credentials must be stored in Keychain rather than committed or
passed through chat.
