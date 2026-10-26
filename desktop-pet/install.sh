#!/usr/bin/env bash
# 把 MeloPet 桌宠「安装」到本机：
#   1) 生成应用图标并打包成 ~/Applications/MeloPet.app（可双击启动）
#   2) 注册 LaunchAgent，登录时自动启动（可随时卸载）
# 桌宠代码与 Python 环境仍引用验证包内的 desktop-pet 目录（不复制 1.6G 依赖）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PY="$PROJECT_DIR/.validation/desktop-pet-venv/bin/python"
PET_PY="$SCRIPT_DIR/pet.py"
APP_DIR="$HOME/Applications/MeloPet.app"
LABEL="com.melopet.pet"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

"$SCRIPT_DIR/bootstrap.sh"

echo "[1/5] 生成应用图标"
"$PY" "$SCRIPT_DIR/render-icon.py"

ICONSET="$(mktemp -d)/MeloPet.iconset"
mkdir -p "$ICONSET"
SRC="$SCRIPT_DIR/MeloPet-icon.png"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
ICNS="$(mktemp -d)/MeloPet.icns"
iconutil -c icns "$ICONSET" -o "$ICNS"

echo "[2/5] 打包 MeloPet.app"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$ICNS" "$APP_DIR/Contents/Resources/AppIcon.icns"
cat > "$APP_DIR/Contents/Info.plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>MeloPet</string>
  <key>CFBundleDisplayName</key><string>MeloPet 音灵桌宠</string>
  <key>CFBundleIdentifier</key><string>com.melopet.pet</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>MeloPet</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST_EOF
cat > "$APP_DIR/Contents/MacOS/MeloPet" <<LAUNCH_EOF
#!/usr/bin/env bash
exec "$PY" "$PET_PY" >> "$SCRIPT_DIR/pet.log" 2>&1
LAUNCH_EOF
chmod +x "$APP_DIR/Contents/MacOS/MeloPet"

echo "[3/5] 注册 LaunchAgent（登录自启动）"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$PET_PY</string>
  </array>
  <key>WorkingDirectory</key><string>$SCRIPT_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$SCRIPT_DIR/pet.log</string>
  <key>StandardErrorPath</key><string>$SCRIPT_DIR/pet.log</string>
</dict>
</plist>
PLIST_EOF

echo "[4/5] 启动 LaunchAgent"
# 先停掉可能的手动实例，避免与 launchd 托管实例重复
if [[ -f "$SCRIPT_DIR/pet.lock" ]]; then
  PET_PID="$(tr -dc '0-9' < "$SCRIPT_DIR/pet.lock")"
  if [[ -n "$PET_PID" ]]; then
    kill "$PET_PID" 2>/dev/null || true
  fi
fi
rm -f "$SCRIPT_DIR/pet.lock"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
  || launchctl load -w "$PLIST" 2>/dev/null || true

echo "[5/5] 完成"
sleep 2
if [[ -f "$SCRIPT_DIR/pet.lock" ]]; then
  PET_PID="$(tr -dc '0-9' < "$SCRIPT_DIR/pet.lock")"
else
  PET_PID=""
fi
if [[ -n "$PET_PID" ]] && kill -0 "$PET_PID" 2>/dev/null; then
  echo "✅ 桌宠已安装并运行（PID ${PET_PID}）"
else
  echo "⚠️ 桌宠进程未检测到，请手动双击 ~/Applications/MeloPet.app 或运行："
  echo "   $SCRIPT_DIR/run-pet.sh"
fi
echo
echo "安装位置："
echo "  应用：$APP_DIR"
echo "  登录自启动：$PLIST"
echo "  日志：$SCRIPT_DIR/pet.log"
echo "卸载：$SCRIPT_DIR/uninstall.sh"
