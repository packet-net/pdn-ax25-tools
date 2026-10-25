#!/usr/bin/env python3
"""Fails if any download bundles a .NET 10 runtime older than the latest patch.

Every tool is self-contained, so each download carries the runtime of whichever SDK built it,
and an operating system update never patches it. A runner with a stale SDK would ship months of
missed security fixes and nothing would notice. This reads the runtime version out of each
program and compares it with the latest 10.0 runtime Microsoft lists.
A newer one passes: an SDK or workload can reach nuget.org a few days before the release index lists that patch
(the WebAssembly workload did, 10.0.14 on 2026-10-25), and the point is to catch a stale runner, not one ahead of the index.

The NativeAOT builds have no deps.json to read, so the version is the "10.0.N-servicing.<build>+<sha>"
string the runtime's own build stamps into the binary (the armhf build carries the same string,
and its runtimepack entry agrees). It follows the SDK that built the tool: a stale SDK yields a
stale string, and an SDK that stamps nothing recognisable (or several different versions) fails the check rather than passing it.

Usage: scripts/check-bundled-runtime.py <download.deb|download.tar.gz|download.zip>...
"""
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath

INDEX = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json"
CHANNEL = "10.0"
TOOLS = ("axcall", "axsocks", "axinetd", "axlisten", "axtun")
PROGRAMS = TOOLS + tuple(t + ".exe" for t in TOOLS)
RUNTIME = re.compile(rb"(\d+\.\d+\.\d+)-servicing\.\d+\.\d+\+[0-9a-f]{40}")


def latest_runtime():
    with urllib.request.urlopen(INDEX, timeout=30) as response:
        index = json.load(response)
    for channel in index["releases-index"]:
        if channel["channel-version"] == CHANNEL:
            return channel["latest-runtime"]
    sys.exit(f"no .NET {CHANNEL} channel in {INDEX}")


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def program_bytes(package):
    if package.suffix == ".zip":
        # Compress-Archive writes backslash separators, hence PureWindowsPath, which takes both.
        with zipfile.ZipFile(package) as z:
            return [(n, z.read(n)) for n in z.namelist() if PureWindowsPath(n).name in PROGRAMS]
    if package.name.endswith(".tar.gz"):
        with tarfile.open(package) as t:
            return [(m.name, t.extractfile(m).read())
                    for m in t.getmembers() if m.isfile() and Path(m.name).name in PROGRAMS]
    # Only usr/lib, where the program is: axtun also installs an example file named axtun.
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["dpkg-deb", "-x", str(package), tmp], check=True)
        return [(str(p.relative_to(tmp)), p.read_bytes())
                for p in Path(tmp, "usr/lib").rglob("*") if p.name in PROGRAMS and p.is_file() and not p.is_symlink()]


def main(packages):
    if not packages:
        sys.exit(__doc__)
    want = latest_runtime()
    print(f"latest .NET {CHANNEL} runtime: {want}")
    bad = False
    for package in map(Path, packages):
        programs = program_bytes(package)
        if not programs:
            print(f"::error::{package.name}: no program found to check")
            bad = True
        for name, data in programs:
            found = sorted({m.decode() for m in RUNTIME.findall(data)})
            print(f"{package.name}: {name} bundles {', '.join(found) or 'nothing recognisable'}")
            if len(found) != 1:
                print(f"::error::{package.name} bundles .NET runtime {', '.join(found) or 'unknown'}, "
                      f"not one recognisable version. Check how it was built.")
                bad = True
            elif version_key(found[0]) < version_key(want):
                print(f"::error::{package.name} bundles .NET runtime {found[0]}, older than {want}. "
                      "Update the .NET 10 SDK on the runner that built it.")
                bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
