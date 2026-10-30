#!/usr/bin/env bash
# Package and install the claim-ledger VS Code extension into this devcontainer.
#
#   bash scripts/ledger/install-extension.sh   # then: Developer: Reload Window
#
# Builds a real .vsix and installs it with the `code` CLI. An earlier version of
# this script copied the folder into ~/.vscode-server/extensions and added an
# entry to extensions.json by hand -- VS Code SCANNED that (it even logs
# "Extensions added from another source") but never loaded it, and no commands
# appeared. Do not go back to that shortcut.
#
# vsce is not used: it needs npm from the network, which the firewall blocks.
# A .vsix is just a zip with a vsixmanifest, so we build it with python.
#
# ~/.vscode-server is a Docker named volume, so re-run this after the volume is
# recreated. The source of truth is scripts/ledger/vscode-extension/ in the repo.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/vscode-extension" && pwd)"
OUT="$(mktemp -d)"; trap 'rm -rf "$OUT"' EXIT

VSIX="$(python3 - "$SRC" "$OUT" <<'PY'
import json, pathlib, sys, zipfile
src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
p = json.loads((src / "package.json").read_text())
name, pub, ver = p["name"], p["publisher"], p["version"]

# The panel is a separate .vsix and has no way to know where this repo was
# cloned, so it used to hunt for ledger.py and, failing, ask. But THIS script
# is being run FROM the repo -- the path is right here. Bake it in as the
# setting's default, and the install is self-configuring.
#
# Only into the packaged copy, never back into the working tree: the path is
# this machine's, and committing it would hand everyone else a wrong one.
# It is a default, so an explicit user setting still wins, and the extension
# checks the file exists before using it -- a moved clone falls back to the
# search rather than breaking.
script = (src.parent / "ledger.py").resolve()
if script.exists():
    props = p.setdefault("contributes", {}).setdefault("configuration", {}) \
             .setdefault("properties", {})
    prop = props.setdefault("claudeLedger.scriptPath", {"type": "string"})
    prop["default"] = str(script)
    print(f"  baked scriptPath default: {script}", file=sys.stderr)
manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{ver}" Publisher="{pub}" />
    <DisplayName>{p["displayName"]}</DisplayName>
    <Description xml:space="preserve">{p["description"]}</Description>
    <Categories>Other</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{p["engines"]["vscode"]}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace" />
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code" /></Installation>
  <Dependencies/>
  <Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest"
           Path="extension/package.json" Addressable="true" /></Assets>
</PackageManifest>
'''
ctypes = '''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="js" ContentType="application/javascript"/>
  <Default Extension="vsixmanifest" ContentType="text/xml"/>
</Types>
'''
vsix = out / f"{pub}.{name}-{ver}.vsix"
with zipfile.ZipFile(vsix, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("extension.vsixmanifest", manifest)
    z.writestr("[Content_Types].xml", ctypes)
    # Every .js at the extension root, not a hardcoded pair: a module added
    # later (dictation.js was the first) would otherwise be silently missing
    # from the .vsix and fail at require() time, in the user's editor.
    files = ["package.json"] + sorted(f.name for f in src.glob("*.js"))
    for f in files:
        if f == "package.json":
            z.writestr("extension/package.json", json.dumps(p, indent=2))
        else:
            z.write(src / f, f"extension/{f}")
print(vsix)
PY
)"

code --install-extension "$VSIX" --force
echo
echo "installed. Now: Cmd+Shift+P -> Developer: Reload Window"
echo "then:          Cmd+Shift+P -> 'Ledger: review'"
