#!/bin/sh
set -eu

FRAMEWORK="${PROJECT_DIR}/Python.xcframework"
APP_SOURCE="${PROJECT_DIR}/app"
BUILD_UTILS="${FRAMEWORK}/build/build_utils.sh"

if [ ! -f "${BUILD_UTILS}" ]; then
    echo "Missing Python-Apple-support build utilities: ${BUILD_UTILS}" >&2
    exit 1
fi
if [ ! -f "${APP_SOURCE}/main.py" ]; then
    echo "Expected the Python app entry point at ${APP_SOURCE}/main.py" >&2
    exit 1
fi

. "${BUILD_UTILS}"
install_python "${FRAMEWORK}" "${APP_SOURCE}"
