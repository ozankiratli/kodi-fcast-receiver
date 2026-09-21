#!/usr/bin/env bash
#
# Refuse a release whose <news> element still describes the previous one.
#
# Usage: dev/scripts/check-news.sh
#
# Kodi shows <news> from addon.xml in the add-on's information dialog, and it is the
# only release note most people ever see -- CHANGELOG.md is not shipped and the GitHub
# release body is not reachable from Kodi. It is also the one thing a version bump
# cannot write for you, because forty words for that dialog are not an extract of
# anything.
#
# So this compares it against the same element at the previous tag. Unchanged means
# nobody wrote one for this version, and the release stops.
#
# Passes with nothing to compare against: a first release, or a previous tag from
# before addon.xml existed.

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"

# The <news> body as one line, so that reindenting it does not read as a rewrite.
news_of() {
    awk '
        { all = all $0 "\n" }
        END {
            if (all !~ /<news>/ || all !~ /<\/news>/) exit 1
            sub(/^.*<news>/, "", all)
            sub(/<\/news>.*$/, "", all)
            gsub(/[ \t\n]+/, " ", all)
            sub(/^ +/, "", all)
            sub(/ +$/, "", all)
            print all
        }
    '
}

CURRENT=$(news_of < addon.xml) || {
    echo "check-news.sh: addon.xml has no <news> element" >&2; exit 1; }
[ -n "$CURRENT" ] || { echo "check-news.sh: <news> is empty" >&2; exit 1; }

# The newest tag that is not whatever is being released now. On a tag push HEAD is
# that tag, so describe from its parent.
PREVIOUS_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || true)
if [ -z "$PREVIOUS_TAG" ]; then
    echo "  ok  <news> is set, and there is no previous release to compare it with"
    exit 0
fi

PREVIOUS=$(git show "$PREVIOUS_TAG:addon.xml" 2>/dev/null | news_of || true)
if [ -z "$PREVIOUS" ]; then
    echo "  ok  <news> is set, and $PREVIOUS_TAG carried none to compare it with"
    exit 0
fi

if [ "$CURRENT" = "$PREVIOUS" ]; then
    echo "<news> in addon.xml is word for word what $PREVIOUS_TAG published:" >&2
    echo >&2
    echo "  $CURRENT" >&2
    echo >&2
    echo "That is what Kodi shows people in the add-on's information dialog, so" >&2
    echo "shipping it unchanged announces the previous release. Write this version's" >&2
    echo "into <news> and tag again." >&2
    exit 1
fi

echo "  ok  <news> has been written for this version, and differs from $PREVIOUS_TAG"
