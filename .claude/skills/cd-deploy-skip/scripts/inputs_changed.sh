#!/usr/bin/env bash
# cd-deploy-skip: local copy of the template's allow-list filter.
# stdin: changed file names, one per line. env: IMAGE_INPUTS_REGEX (extended regex).
# stdout: image=true (build/deploy) or image=false (no image input changed).
# Empty input fails open (true). Keep identical to the filter line in templates/deploy-changes-job.yml.
set -u
: "${IMAGE_INPUTS_REGEX:?set IMAGE_INPUTS_REGEX}"
CHANGED="$(cat)"
if [ -z "$CHANGED" ]; then echo "image=true"; exit 0; fi
IMAGE_FILES="$(printf '%s\n' "$CHANGED" | grep -E "$IMAGE_INPUTS_REGEX" || true)"
if [ -n "$IMAGE_FILES" ]; then echo "image=true"; else echo "image=false"; fi
