#!/bin/bash
# 在 macOS 上（或 GitHub Actions 的 macOS runner）打 Mac 版安装包
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "== 依赖 =="
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r requirements.txt pyinstaller >/dev/null

echo "== 打包命令行 =="
rm -rf build/pyi dist
python3 -m PyInstaller --onefile --name legalredact --distpath dist --workpath build/pyi --specpath build redact.py

echo "== 组装 =="
rm -rf package && mkdir -p package
cp dist/legalredact package/
cp -R "mac/一键脱敏.workflow" package/
cp "mac/安装.command" package/
chmod +x package/legalredact package/安装.command

echo "== 生成 .app（可拖拽） =="
osacompile -o "package/法律脱敏.app" "mac/拖拽脱敏.applescript"

echo "== 压缩 =="
rm -f "法律脱敏-Mac.zip"
(cd package && zip -qry "$ROOT/法律脱敏-Mac.zip" .)
ls -lh "法律脱敏-Mac.zip"
