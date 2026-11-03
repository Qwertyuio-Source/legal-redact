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
# 用 python 打包，确保中文文件名带 UTF-8 标记（Windows/Mac 解压都不乱码）
python3 - "$ROOT" <<'PY'
import os, sys, zipfile
root = os.path.join(sys.argv[1], "package")
out = os.path.join(sys.argv[1], "法律脱敏-Mac.zip")
if os.path.exists(out): os.remove(out)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for base, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            z.write(p, os.path.relpath(p, root))
print("zipped:", out)
PY
ls -lh "法律脱敏-Mac.zip"
