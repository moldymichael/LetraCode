#!/usr/bin/env bash
set -euo pipefail

readonly APP_ID="io.letracode.LetraCode"
readonly MARKER_CONTENT="$APP_ID"

usage() {
    cat >&2 <<'EOF'
Usage: ./install.sh [--no-deps]

Install LetraCode for the current user. The normal installation asks sudo/dnf
to install Fedora's native Python and Qt dependencies. --no-deps is only for a
development or test environment where those dependencies are already present.
LETRACODE_PYTHON may explicitly select a Python 3.11+ interpreter for testing.
EOF
}

install_deps=true
for argument in "$@"; do
    case "$argument" in
        --no-deps) install_deps=false ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
for required in letracode/__main__.py letracode/app.py \
    letracode/assets/io.letracode.LetraCode.svg uninstall.sh README.md LICENSE \
    packaging/io.letracode.LetraCode.metainfo.xml; do
    if [[ ! -f "$source_dir/$required" ]]; then
        printf 'Installer source is incomplete: missing %s\n' "$required" >&2
        exit 1
    fi
done

if [[ -z "${HOME:-}" || "$HOME" != /* ]]; then
    printf 'HOME must be an absolute path.\n' >&2
    exit 1
fi

if $install_deps; then
    printf 'Installing Fedora system dependencies (sudo will ask for your password if needed)…\n'
    sudo dnf install -y python3 python3-pyside6 python3-pypdf
else
    printf 'Skipping dependency installation because --no-deps was requested.\n'
fi

python_request="${LETRACODE_PYTHON:-python3}"
if [[ "$python_request" == */* ]]; then
    if [[ ! -x "$python_request" ]]; then
        printf 'Selected LETRACODE_PYTHON is not executable: %s\n' "$python_request" >&2
        exit 1
    fi
    python_bin="$(cd -- "$(dirname -- "$python_request")" && pwd -P)/$(basename -- "$python_request")"
else
    python_bin="$(command -v -- "$python_request" || true)"
    if [[ -z "$python_bin" ]]; then
        printf 'Python interpreter not found: %s\n' "$python_request" >&2
        exit 1
    fi
fi

if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    printf 'LetraCode requires Python 3.11 or newer.\n' >&2
    exit 1
fi
if $install_deps && ! "$python_bin" -c 'from PySide6 import QtWidgets; import pypdf'; then
    printf 'Fedora dependencies were installed, but the selected Python cannot import PySide6.QtWidgets and pypdf.\n' >&2
    printf 'Unset LETRACODE_PYTHON to use Fedora system Python, then run the installer again.\n' >&2
    exit 1
fi
if ! PYTHONPATH="$source_dir${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -P -m letracode --version >/dev/null; then
    printf 'The LetraCode source failed its launch validation; the existing install was not changed.\n' >&2
    exit 1
fi

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_home="$HOME/.local/bin"
mkdir -p -- "$data_home" "$bin_home"
data_home="$(cd -- "$data_home" && pwd -P)"
bin_home="$(cd -- "$bin_home" && pwd -P)"

app_dir="$data_home/letracode-app"
launcher="$bin_home/letracode"
desktop_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/scalable/apps"
metainfo_dir="$data_home/metainfo"
desktop_file="$desktop_dir/$APP_ID.desktop"

if [[ -e "$launcher" || -L "$launcher" ]]; then
    if [[ ! -f "$launcher" ]] || \
       ! head -n 2 -- "$launcher" | grep -Fq "Managed by LetraCode installer: $APP_ID"; then
        printf 'Refusing to replace %s because it is not managed by the LetraCode installer.\n' "$launcher" >&2
        exit 1
    fi
fi
if [[ -e "$desktop_file" || -L "$desktop_file" ]]; then
    if [[ ! -f "$desktop_file" ]] || ! grep -Fxq 'X-LetraCode-Managed=true' "$desktop_file"; then
        printf 'Refusing to replace %s because it is not managed by the LetraCode installer.\n' "$desktop_file" >&2
        exit 1
    fi
fi

if [[ -L "$app_dir" ]]; then
    printf 'Refusing to replace a symbolic-link application directory: %s\n' "$app_dir" >&2
    exit 1
fi
if [[ -e "$app_dir" ]]; then
    if [[ ! -f "$app_dir/.letracode-install" ]] || \
       [[ "$(cat -- "$app_dir/.letracode-install")" != "$MARKER_CONTENT" ]]; then
        printf 'Refusing to replace %s because it is not a LetraCode installation.\n' "$app_dir" >&2
        exit 1
    fi
fi

stage="$(mktemp -d "$data_home/.letracode-app.new.XXXXXX")"
backup=""
cleanup() {
    if [[ -n "$stage" && -d "$stage" ]]; then rm -rf -- "$stage"; fi
    if [[ -n "$backup" && -d "$backup" ]]; then rm -rf -- "$backup"; fi
}
trap cleanup EXIT

cp -a -- "$source_dir/letracode" "$stage/letracode"
cp -- "$source_dir/uninstall.sh" "$source_dir/README.md" "$source_dir/LICENSE" "$stage/"
if [[ -d "$source_dir/docs" ]]; then
    cp -a -- "$source_dir/docs" "$stage/docs"
fi
printf '%s\n' "$MARKER_CONTENT" > "$stage/.letracode-install"
chmod 0644 "$stage/.letracode-install"
chmod 0755 "$stage/uninstall.sh"
PYTHONPATH="$stage${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -P -m letracode --version >/dev/null

if [[ -d "$app_dir" ]]; then
    backup="$(mktemp -d "$data_home/.letracode-app.old.XXXXXX")"
    rmdir -- "$backup"
    mv -- "$app_dir" "$backup"
fi
if ! mv -- "$stage" "$app_dir"; then
    if [[ -n "$backup" && -d "$backup" ]]; then mv -- "$backup" "$app_dir"; backup=""; fi
    printf 'Could not activate the new installation. The previous version was restored.\n' >&2
    exit 1
fi
stage=""

launcher_tmp="$(mktemp "$bin_home/.letracode-launcher.XXXXXX")"
{
    printf '#!/usr/bin/env bash\n'
    printf '# Managed by LetraCode installer: %s\n' "$APP_ID"
    printf 'readonly app_dir=%q\n' "$app_dir"
    printf 'readonly python_bin=%q\n' "$python_bin"
    printf 'export PYTHONPATH="$app_dir${PYTHONPATH:+:$PYTHONPATH}"\n'
    printf 'exec "$python_bin" -P -m letracode "$@"\n'
} > "$launcher_tmp"
chmod 0755 "$launcher_tmp"
mv -f -- "$launcher_tmp" "$launcher"

mkdir -p -- "$desktop_dir" "$icon_dir" "$metainfo_dir"
desktop_tmp="$(mktemp "$desktop_dir/.letracode-desktop.XXXXXX")"
desktop_exec="${launcher//\\/\\\\}"
desktop_exec="${desktop_exec//\"/\\\"}"
desktop_exec="${desktop_exec//\$/\\$}"
desktop_exec="${desktop_exec//\`/\\\`}"
cat > "$desktop_tmp" <<EOF
[Desktop Entry]
Type=Application
Name=LetraCode
GenericName=Local AI Chat
Comment=Chat privately with a local model and your project files
Exec="$desktop_exec"
Icon=$APP_ID
Terminal=false
Categories=Development;Utility;
Keywords=AI;Chat;Local;LLM;Code;
StartupNotify=true
StartupWMClass=LetraCode
X-LetraCode-Managed=true
EOF
chmod 0644 "$desktop_tmp"
mv -f -- "$desktop_tmp" "$desktop_file"
install -m 0644 -- "$app_dir/letracode/assets/$APP_ID.svg" "$icon_dir/$APP_ID.svg"
install -m 0644 -- "$source_dir/packaging/$APP_ID.metainfo.xml" "$metainfo_dir/$APP_ID.metainfo.xml"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$desktop_dir" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q "$data_home/icons/hicolor" >/dev/null 2>&1 || true
fi

if [[ -n "$backup" && -d "$backup" ]]; then rm -rf -- "$backup"; backup=""; fi
printf '\nInstallation complete. Start LetraCode from the application launcher or run:\n  %s\n' "$launcher"
printf 'Chats and settings are stored separately in: %s\n' "$data_home/letracode"
