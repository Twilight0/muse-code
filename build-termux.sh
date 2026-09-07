#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Read version metadata from PKGBUILD
REALVER=$(grep -E '^_realver=' PKGBUILD | cut -d'=' -f2 | tr -d '"' | tr -d "'")
PKGVER=$(grep -E '^pkgver=' PKGBUILD | cut -d'=' -f2 | tr -d '"' | tr -d "'")
PKGREL=$(grep -E '^pkgrel=' PKGBUILD | cut -d'=' -f2 | tr -d '"' | tr -d "'")
DEB_VERSION="${PKGVER}-${PKGREL}"
ARCH="aarch64"

echo "=== Building Muse Code Termux Package (${DEB_VERSION} [${ARCH}]) ==="

DIST_DIR="${SCRIPT_DIR}/dist"
BUILD_DIR="${SCRIPT_DIR}/build-termux"
PKG_DIR="${BUILD_DIR}/muse-code_${DEB_VERSION}_${ARCH}"
PREFIX="data/data/com.termux/files/usr"

rm -rf "${BUILD_DIR}"
mkdir -p "${DIST_DIR}" "${PKG_DIR}/DEBIAN" "${PKG_DIR}/${PREFIX}/bin" "${PKG_DIR}/${PREFIX}/lib/muse"

# Fetch upstream aarch64 binary if not present
AARCH64_BIN="${SCRIPT_DIR}/muse-code-${PKGVER}-aarch64"
if [[ ! -f "${AARCH64_BIN}" ]]; then
  echo "Downloading upstream aarch64 binary (${REALVER})..."
  URL="https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=${REALVER}&file=muse-aarch64-linux"
  curl -fSL "${URL}" -o "${AARCH64_BIN}"
fi

# Verify checksum against PKGBUILD
EXPECTED_SHA=$(grep -E "sha256sums_aarch64=" PKGBUILD | grep -Po "[a-f0-9]{64}")
ACTUAL_SHA=$(sha256sum "${AARCH64_BIN}" | cut -d' ' -f1)
if [[ "${ACTUAL_SHA}" != "${EXPECTED_SHA}" ]]; then
  echo "Error: Checksum mismatch for ${AARCH64_BIN}" >&2
  echo "Expected: ${EXPECTED_SHA}" >&2
  echo "Actual:   ${ACTUAL_SHA}" >&2
  exit 1
fi
echo "SHA256 verified: ${ACTUAL_SHA}"

echo "Installing files into Termux package layout..."
install -Dm755 "${AARCH64_BIN}" "${PKG_DIR}/${PREFIX}/lib/muse/muse"
install -Dm755 "${SCRIPT_DIR}/muse.sh" "${PKG_DIR}/${PREFIX}/bin/muse"
install -Dm755 "${SCRIPT_DIR}/muse-session" "${PKG_DIR}/${PREFIX}/lib/muse/muse-session"
install -Dm755 "${SCRIPT_DIR}/muse-mcp" "${PKG_DIR}/${PREFIX}/lib/muse/muse-mcp"

# Create symlinks in $PREFIX/bin
ln -s muse "${PKG_DIR}/${PREFIX}/bin/muse-code"
ln -s /data/data/com.termux/files/usr/lib/muse/muse-session "${PKG_DIR}/${PREFIX}/bin/muse-session"
ln -s /data/data/com.termux/files/usr/lib/muse/muse-mcp "${PKG_DIR}/${PREFIX}/bin/muse-mcp"

# Calculate installed size in KB
INSTALLED_SIZE=$(du -sk "${PKG_DIR}/${PREFIX}" | cut -f1)

# Write DEBIAN/control
cat <<EOF > "${PKG_DIR}/DEBIAN/control"
Package: muse-code
Version: ${DEB_VERSION}
Architecture: ${ARCH}
Maintainer: Twilight <twilight@aliveos.org>
Installed-Size: ${INSTALLED_SIZE}
Depends: python, ca-certificates, proot
Section: devel
Priority: optional
Homepage: https://dev.meta.ai
Description: Terminal-based AI coding agent powered by Meta's Muse Spark (dev.meta.ai)
 Packed with AVX2 legacy fallback, interactive curses TUI session picker,
 and multi-agent MCP configuration management.
EOF

DEB_FILE="${DIST_DIR}/muse-code_${DEB_VERSION}_termux_${ARCH}.deb"

if command -v dpkg-deb >/dev/null 2>&1; then
  dpkg-deb --build --root-owner-group "${PKG_DIR}" "${DEB_FILE}"
else
  echo "Assembling .deb archive using tar, xz, and ar..."
  TMP_BUILD="$(mktemp -d)"
  trap 'rm -rf "${TMP_BUILD}"' EXIT

  printf "2.0\n" > "${TMP_BUILD}/debian-binary"

  # 1. Create control.tar.xz
  tar -C "${PKG_DIR}/DEBIAN" --owner=0 --group=0 --numeric-owner -cf - ./control | xz -T0 > "${TMP_BUILD}/control.tar.xz"

  # 2. Create data.tar.xz (only payload under ./data)
  tar -C "${PKG_DIR}" --owner=0 --group=0 --numeric-owner -cf - ./data | xz -T0 > "${TMP_BUILD}/data.tar.xz"

  # 3. Assemble with ar
  rm -f "${DEB_FILE}"
  ar -rcs "${DEB_FILE}" "${TMP_BUILD}/debian-binary" "${TMP_BUILD}/control.tar.xz" "${TMP_BUILD}/data.tar.xz"
fi

echo "=== Termux package created successfully ==="
echo "Output: ${DEB_FILE}"
ls -lh "${DEB_FILE}"
