#!/bin/bash
set -euo pipefail
# Builds calendar-helper as a minimal .app bundle so macOS can grant it Calendar access.
cd "$(dirname "$0")"
APP="calendar-helper.app"
BIN="calendar-helper"

swiftc -O -o "$BIN" CalendarHelper.swift
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mv "$BIN" "$APP/Contents/MacOS/$BIN"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>calendar-helper</string>
    <key>CFBundleIdentifier</key>
    <string>local.minitoo.calendar-helper</string>
    <key>CFBundleName</key>
    <string>calendar-helper</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSCalendarsFullAccessUsageDescription</key>
    <string>Show today's next event on the MiniToo dashboard.</string>
    <key>NSCalendarsUsageDescription</key>
    <string>Show today's next event on the MiniToo dashboard.</string>
</dict>
</plist>
PLIST

codesign --force --sign - --deep "$APP"
echo "Built: $PWD/$APP"
