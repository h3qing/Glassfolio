#!/usr/bin/env bash
# Build Glassfolio.app (and a .dmg): web UI → frozen analysis service → Tauri bundle.
# Output: src-tauri/target/release/bundle/{macos,dmg}/
set -euo pipefail
cd "$(dirname "$0")/.."

TRIPLE=$(rustc -vV | sed -n 's/^host: //p')
echo "==> Web UI"
pnpm -C web install --silent --frozen-lockfile
pnpm -C web build >/dev/null

echo "==> DuckLake extension (bundled so the app never downloads it)"
EXT=$(uv run --locked --quiet python -c "
import duckdb, pathlib
c = duckdb.connect(); c.execute('INSTALL ducklake')
v, plat = duckdb.__version__, c.execute('PRAGMA platform').fetchone()[0]
print(pathlib.Path.home() / '.duckdb' / 'extensions' / f'v{v}' / plat / 'ducklake.duckdb_extension')")
test -f "$EXT"

echo "==> Analysis service (PyInstaller)"
uv run --locked --quiet pyinstaller --noconfirm --clean --onefile --name glassfolio-server \
  --distpath build/sidecar --workpath build/pyinstaller --specpath build \
  --collect-submodules uvicorn --collect-submodules glassfolio --collect-data glassfolio \
  --collect-all pypdfium2 --collect-all pdfplumber --hidden-import Vision --hidden-import Quartz \
  --add-data "$PWD/web/dist:web/dist" --add-data "$PWD/evals:evals" \
  --add-data "$PWD/tests/golden:tests/golden" \
  --log-level WARN scripts/sidecar_entry.py
mkdir -p src-tauri/binaries
cp build/sidecar/glassfolio-server "src-tauri/binaries/glassfolio-server-$TRIPLE"
touch src-tauri/build.rs  # re-embed the service's SHA-256 in the app
# Shipped as a Tauri resource, untouched: re-signing it would break DuckDB's own signature.
cp "$EXT" src-tauri/binaries/ducklake.duckdb_extension

echo "==> Desktop app (Tauri)"
(cd src-tauri && cargo tauri build --bundles app --config tauri.bundle.conf.json)

echo "==> Disk image"
APP=src-tauri/target/release/bundle/macos/Glassfolio.app
mkdir -p dist && rm -f dist/Glassfolio.dmg
hdiutil create -quiet -volname Glassfolio -srcfolder "$APP" -ov -format UDZO dist/Glassfolio.dmg
ls -d "$APP" dist/Glassfolio.dmg
