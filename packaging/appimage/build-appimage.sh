#!/usr/bin/env bash
#
# Build a self-contained Projectum AppImage for x86-64 Linux.
#
# Bundles a relocatable manylinux Python with PySide6, yt-dlp and the
# Projectum wheel via `python-appimage`. Output:
#
#     build/appimage/Projectum-x86_64.AppImage
#
# Requirements: python-appimage, build  (pip install python-appimage build)
# Used by both `make`-style local builds and the GitHub release workflow.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
PYVER="${PYVER:-3.11}"
BUILD="${ROOT}/build/appimage"
RECIPE="${BUILD}/recipe"

# appimagetool ships as a type-2 AppImage; self-extract instead of FUSE-mount
# so the build works on CI runners that have no /dev/fuse.
export APPIMAGE_EXTRACT_AND_RUN=1
export ARCH="${ARCH:-x86_64}"

rm -rf "${BUILD}"
mkdir -p "${RECIPE}"

echo ">> Building Projectum wheel"
python -m pip wheel "${ROOT}" --no-deps -w "${RECIPE}"

echo ">> Assembling AppImage recipe"
cp "${HERE}/projectum.desktop" "${RECIPE}/"
cp "${HERE}/projectum.png" "${RECIPE}/"
WHEEL="$(ls "${RECIPE}"/projectum-*.whl)"
cat > "${RECIPE}/requirements.txt" <<EOF
PySide6-Essentials>=6.5
yt-dlp>=2024.0
${WHEEL}
EOF

echo ">> Building AppImage (python ${PYVER})"
cd "${BUILD}"
python -m python_appimage build app -p "${PYVER}" "${RECIPE}"

APPIMAGE="$(ls "${BUILD}"/*.AppImage)"
chmod +x "${APPIMAGE}"
echo ">> Built: ${APPIMAGE}"
