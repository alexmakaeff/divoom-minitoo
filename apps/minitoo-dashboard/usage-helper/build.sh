#!/bin/bash
set -euo pipefail
# Builds usage-helper. Rebuilding changes its ad-hoc signature, so macOS asks
# for Keychain access again on the next run.
cd "$(dirname "$0")"
swiftc -O -o usage-helper UsageHelper.swift
codesign --force --sign - usage-helper
echo "Built: $PWD/usage-helper"
