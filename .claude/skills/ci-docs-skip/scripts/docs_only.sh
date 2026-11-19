#!/usr/bin/env bash
# ci-docs-skip: local copy of the template's filter logic.
# stdin: changed file names, one per line. env: DOCS_ONLY_REGEX (extended regex).
# stdout: code=true (CI must run) or code=false (docs-only). Empty input fails open (true).
# Keep identical to the filter part of templates/changes-job.yml.
set -u
: "${DOCS_ONLY_REGEX:?set DOCS_ONLY_REGEX}"
CHANGED="$(cat)"
if [ -z "$CHANGED" ]; then echo "code=true"; exit 0; fi
CODE_FILES="$(printf '%s\n' "$CHANGED" | grep -vE "$DOCS_ONLY_REGEX" || true)"
if [ -n "$CODE_FILES" ]; then echo "code=true"; else echo "code=false"; fi
