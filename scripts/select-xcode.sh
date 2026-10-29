#!/bin/bash
# Source this file to choose an already-installed, App Store-compatible toolchain.
# Does not download Xcode, accept licenses, or change global xcode-select settings.
set -euo pipefail
evolink_select_xcode() {
  if [ "$(uname -s)" != "Darwin" ]; then
    echo 'An authorized Mac is required; no iOS build was executed.' >&2
    return 1
  fi
  local candidate version sdk major sdk_major default_dir
  default_dir="${DEVELOPER_DIR:-$(xcode-select -p 2>/dev/null || true)}"
  for candidate in "$default_dir" /Applications/Xcode*.app/Contents/Developer; do
    case "$candidate" in *[Bb]eta*) continue ;; esac
    [ -x "$candidate/usr/bin/xcodebuild" ] || continue
    version=$(DEVELOPER_DIR="$candidate" "$candidate/usr/bin/xcodebuild" -version 2>/dev/null | awk '/^Xcode / {print $2}') || continue
    major=${version%%.*}
    case "$major" in ''|*[!0-9]*) continue ;; esac
    [ "$major" -ge 26 ] || continue
    sdk=$(DEVELOPER_DIR="$candidate" xcrun --sdk iphoneos --show-sdk-version 2>/dev/null) || continue
    sdk_major=${sdk%%.*}
    case "$sdk_major" in ''|*[!0-9]*) continue ;; esac
    [ "$sdk_major" -ge 26 ] || continue
    export DEVELOPER_DIR="$candidate"
    echo "Using installed Xcode $version / iOS SDK $sdk"
    "$candidate/usr/bin/xcodebuild" -version
    return 0
  done
  echo 'No installed stable Xcode 26+ with iOS SDK 26+; no download, license change or build performed.' >&2
  return 1
}
evolink_select_xcode
unset -f evolink_select_xcode
