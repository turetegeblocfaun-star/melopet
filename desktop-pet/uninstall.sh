#!/usr/bin/env bash
# 卸载 MeloPet 桌宠：移除 LaunchAgent 与 ~/Applications/MeloPet.app。
set -euo pipefail

LABEL="com.melopet.pet"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_DIR="$HOME/Applications/MeloPet.app"

echo "停止 LaunchAgent ..."
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true

echo "移除 plist ..."
rm -f "$PLIST"

echo "移除应用 ..."
rm -rf "$APP_DIR"

echo "停止桌宠进程 ..."
pkill -f "desktop-pet/.*pet.py" 2>/dev/null || true

echo "✅ 已卸载 MeloPet 桌宠"
