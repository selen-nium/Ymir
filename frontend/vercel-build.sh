#!/bin/sh
# Vercel build: copy the static app to dist/ and point it at the backend.
# Set YMIR_API_BASE on the Vercel project, e.g. https://<workspace>--ymir-web.modal.run
set -eu
: "${YMIR_API_BASE:?Set YMIR_API_BASE (the Modal API URL) in the Vercel project settings}"
case "$YMIR_API_BASE" in
  https://*|http://localhost*|http://127.0.0.1*) ;;
  *) echo "YMIR_API_BASE must be an https:// URL, got: $YMIR_API_BASE" >&2; exit 1 ;;
esac
rm -rf dist
mkdir dist
cp -R index.html home.html app.html shared vendor dist/
printf "window.YMIR_API_BASE = '%s';\n" "${YMIR_API_BASE%/}" > dist/config.js
echo "Built dist/ for API $YMIR_API_BASE"
