#!/usr/bin/env bash
# ci-docs-skip: table-driven tests for docs_only.sh.
# Usage: DOCS_ONLY_REGEX='<regex>' bash test_docs_only.sh <cases-file>
# Cases file: one case per line, "expected|file1,file2,..." (expected = true or false);
# blank lines and lines starting with # are ignored.
set -u
: "${DOCS_ONLY_REGEX:?set DOCS_ONLY_REGEX}"
CASES="${1:?usage: test_docs_only.sh <cases-file>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
pass=0; fail=0
while IFS='|' read -r expected files || [ -n "$expected" ]; do
  case "$expected" in ''|\#*) continue ;; esac
  got="$(printf '%s\n' "$files" | tr ',' '\n' | bash "$HERE/docs_only.sh")"
  if [ "$got" = "code=$expected" ]; then
    pass=$((pass + 1)); echo "ok    $got  <- $files"
  else
    fail=$((fail + 1)); echo "FAIL  $got (expected code=$expected)  <- $files"
  fi
done < "$CASES"
echo "passed: $pass  failed: $fail"
[ "$fail" -eq 0 ]
