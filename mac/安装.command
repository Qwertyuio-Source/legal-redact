#!/bin/bash
# 法律脱敏 - Mac 安装（双击运行）
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/LegalRedact"

echo "== 法律文书脱敏 · Mac 版安装 =="
echo "安装到：$DEST"
mkdir -p "$DEST"

echo "[1/3] 复制程序..."
cp "$DIR/legalredact" "$DEST/legalredact"
chmod +x "$DEST/legalredact"
xattr -d com.apple.quarantine "$DEST/legalredact" 2>/dev/null || true

echo "[2/3] 安装右键「快速操作」..."
mkdir -p "$HOME/Library/Services"
rm -rf "$HOME/Library/Services/一键脱敏.workflow"
cp -R "$DIR/一键脱敏.workflow" "$HOME/Library/Services/"
xattr -dr com.apple.quarantine "$HOME/Library/Services/一键脱敏.workflow" 2>/dev/null || true

echo "[3/3] 安装拖拽 App..."
rm -rf "/Applications/法律脱敏.app"
cp -R "$DIR/法律脱敏.app" /Applications/ 2>/dev/null || cp -R "$DIR/法律脱敏.app" "$HOME/Applications/"
xattr -dr com.apple.quarantine "/Applications/法律脱敏.app" 2>/dev/null || true

echo
echo "安装完成 ✅"
echo "· 用法一：右键任意文件 → 快速操作 → 一键脱敏"
echo "· 用法二：把文件拖到「法律脱敏」App 图标上"
echo "· 用法三：终端里 legalredact 文件"
echo
echo "首次使用若被系统拦截，到 系统设置 → 隐私与安全性 点「仍要打开」。"
read -p "按回车关闭…" _
