#!/usr/bin/env zsh

URL="$1"

if [ -z "$URL" ]; then
  echo "Usage: $0 <url>"
  exit 1
fi

HASH=$(curl -s "$URL" | openssl dgst -sha384 -binary | base64 -w 0)
echo "sha384-${HASH}"