#!/usr/bin/env bash
set -euo pipefail

readonly APP_ID="io.letracode.LetraCode"

if [[ $# -gt 0 ]]; then
    if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
        printf 'Usage: %s\nRemoves the LetraCode application but keeps chats and settings.\n' "$0"
        exit 0
    fi
    printf 'Usage: %s\n' "$0" >&2
    exit 2
fi
if [[ -z "${HOME:-}" || "$HOME" != /* ]]; then
    printf 'HOME must be an absolute path.\n' >&2
    exit 1
fi

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
mkdir -p -- "$data_home"
data_home="$(cd -- "$data_home" && pwd -P)"
app_dir="$data_home/letracode-app"
launcher="$HOME/.local/bin/letracode"
desktop="$data_home/applications/$APP_ID.desktop"
icon="$data_home/icons/hicolor/scalable/apps/$APP_ID.svg"
metainfo="$data_home/metainfo/$APP_ID.metainfo.xml"

if [[ -L "$app_dir" || ! -f "$app_dir/.letracode-install" ]] || \
   [[ "$(cat -- "$app_dir/.letracode-install" 2>/dev/null || true)" != "$APP_ID" ]]; then
    printf 'Refusing to remove %s because it is not a marked LetraCode installation.\n' "$app_dir" >&2
    exit 1
fi

if [[ -f "$launcher" ]] && head -n 2 -- "$launcher" | grep -Fq "Managed by LetraCode installer: $APP_ID"; then
    rm -f -- "$launcher"
fi
if [[ -f "$desktop" ]] && grep -Fxq 'X-LetraCode-Managed=true' "$desktop"; then
    rm -f -- "$desktop"
fi
rm -f -- "$icon" "$metainfo"
rm -rf -- "$app_dir"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data_home/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q "$data_home/icons/hicolor" >/dev/null 2>&1 || true
fi

printf 'LetraCode was uninstalled. User data was kept in: %s\n' "$data_home/letracode"
