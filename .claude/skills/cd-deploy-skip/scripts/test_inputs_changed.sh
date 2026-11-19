#!/usr/bin/env bash
# cd-deploy-skip: table-driven tests for inputs_changed.sh.
# Usage: IMAGE_INPUTS_REGEX='<regex>' bash test_inputs_changed.sh <cases-file>
# Cases file: one case per line, "expected|file1,file2,..." (expected = true when CD must
# build/deploy); blank lines and lines starting with # are ignored.
set -u
: "${IMAGE_INPUTS_REGEX:?set IMAGE_INPUTS_REGEX}"
CASES="${1:?usage: test_inputs_changed.sh <cases-file>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
pass=0; fail=0
while IFS='|' read -r expected files || [ -n "$expected" ]; do
  case "$expected" in ''|\#*) continue ;; esac
  got="$(printf '%s\n' "$files" | tr ',' '\n' | bash "$HERE/inputs_changed.sh")"
  if [ "$got" = "image=$expected" ]; then
    pass=$((pass + 1)); echo "ok    $got  <- $files"
  else
    fail=$((fail + 1)); echo "FAIL  $got (expected image=$expected)  <- $files"
  fi
done < "$CASES"
echo "passed: $pass  failed: $fail"
[ "$fail" -eq 0 ]
