#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if ! command -v rpmbuild >/dev/null 2>&1; then
    printf 'rpmbuild is required. On Fedora run: sudo dnf install rpm-build\n' >&2
    exit 1
fi

"${LETRACODE_BUILD_PYTHON:-python3}" "$root/packaging/build-release.py" --output-dir "$root/dist"
topdir="${LETRACODE_RPM_TOPDIR:-$root/build/rpmbuild}"
mkdir -p "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp -- "$root/dist/LetraCode-0.5.0.tar.gz" "$topdir/SOURCES/"
cp -- "$root/packaging/letracode.spec" "$topdir/SPECS/"
rpmbuild -ba --define "_topdir $topdir" "$topdir/SPECS/letracode.spec"
mkdir -p "$root/dist"
find "$topdir/RPMS" "$topdir/SRPMS" -type f \( -name '*.rpm' -o -name '*.src.rpm' \) \
    -exec cp -f -- {} "$root/dist/" \;
printf 'RPM artifacts copied to %s\n' "$root/dist"
