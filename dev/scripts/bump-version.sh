#!/usr/bin/env bash
#
# Set the add-on version and open a CHANGELOG section for it.
#
#     dev/scripts/bump-version.sh 1.0.0
#     dev/scripts/bump-version.sh 1.0.0 --dry-run
#
# Changes addon.xml, which is the only place the version is written down, and prepends
# a CHANGELOG section listing every commit since the last tag under a "### Commits"
# heading, with the matching reference-link definition at the foot of the file.
#
# Write this version's notes ABOVE the "### Commits" heading, not over it: the commit
# list stays in the changelog as the record of what actually landed, and the whole
# section becomes the body of the GitHub release. dev/scripts/changelog-section.sh is
# what extracts it, and both release workflows refuse to publish a version the
# changelog does not describe.
#
# Does not commit, tag or push. It prints those commands for you.

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ADDON_XML="$ROOT/addon.xml"
CHANGELOG="$ROOT/CHANGELOG.md"

VERSION=${1:-}
DRY_RUN=${2:-}

die() { echo "$*" >&2; exit 1; }

[ -n "$VERSION" ] || die "usage: $(basename "$0") <version> [--dry-run]
  release:     1.0.0        tagged v1.0.0
  pre-release: 1.0.0~beta1  tagged p1.0.0-beta1"

for f in "$ADDON_XML" "$CHANGELOG"; do
  [ -f "$f" ] || die "ERROR: $f not found"
done

# Kodi's own rules, from VALID_ADDON_VERSION_CHARACTERS in AddonVersion.cpp, plus '-'
# which it reads as the separator before a revision. Anything else is dropped with
# 'is not a valid version' and the add-on reads as 0.0.0.
case "$VERSION" in
  *[!a-zA-Z0-9.+_@~-]*) die "'$VERSION' has characters Kodi will not accept" ;;
esac

# '-' makes what follows a revision, and a revision sorts ABOVE no revision:
# 1.0.0-beta1 is *newer* than 1.0.0 to Kodi, so nobody on the beta would ever be
# offered the release. '~' is the one character that sorts below empty.
case "$VERSION" in
  *-*) die "use '~' rather than '-' for a pre-release: ${VERSION//-/\~}" ;;
esac

# The tag that goes with this version, per the two release workflows: '~' cannot
# appear in a git ref, so it becomes '-' there. A version carrying a '~' is a
# pre-release and belongs to prerelease.yml, which does not publish to the add-on
# repository.
if [[ "$VERSION" == *"~"* ]]; then
  TAG="p${VERSION//\~/-}"
  KIND="pre-release (not published to the repository)"
else
  TAG="v$VERSION"
  KIND="release (published to every installed device)"
fi

CURRENT=$(sed -n 's/.*<addon .*version="\([^"]*\)".*/\1/p' "$ADDON_XML")
[ -n "$CURRENT" ] || die "ERROR: no version found in addon.xml"
[ "$VERSION" != "$CURRENT" ] || die "ERROR: addon.xml is already at $VERSION"
grep -q "^## \[$VERSION\]" "$CHANGELOG" &&
  die "ERROR: CHANGELOG.md already has a [$VERSION] section"

LAST_TAG=$(git -C "$ROOT" describe --tags --abbrev=0 2>/dev/null || true)
if [ -n "$LAST_TAG" ]; then
  RANGE="$LAST_TAG..HEAD"
  SINCE="since $LAST_TAG"
else
  RANGE="HEAD"
  SINCE="from the beginning of the repository"
fi

# --no-merges: a merge commit says nothing a reader wants in a changelog, and the
# commits it brought in are listed on their own. --reverse: oldest first, so the list
# reads in the order the work happened.
COMMITS=$(git -C "$ROOT" log --no-merges --reverse --abbrev=6 --format='- (%h) %s' $RANGE)
[ -n "$COMMITS" ] || die "ERROR: no commits $SINCE - nothing to release"
COUNT=$(printf '%s\n' "$COMMITS" | wc -l)

# Every `## [x.y.z]` heading is a Markdown reference link and renders as literal
# brackets without a definition. The base URL is taken from the newest existing one,
# so it follows the repository rather than being written down twice. The final path
# component is the tag, which here is not always v<numbers>.
LINKBASE=$(sed -n 's|^\[[^]]*\]: \(https://.*\)/[^/]*$|\1|p' "$CHANGELOG" | head -1)
[ -n "$LINKBASE" ] || LINKBASE="https://github.com/ozankiratli/kodi-fcast-receiver/releases/tag"
LINK="[$VERSION]: $LINKBASE/$TAG"

ENTRY="## [$VERSION] - $(date +%F)

_Summary goes here. It becomes the body of the GitHub release._

### Commits

$COMMITS

---
"

echo "version:   $CURRENT -> $VERSION"
echo "tag:       $TAG    $KIND"
echo "commits:   $COUNT $SINCE"

if [ "$DRY_RUN" = "--dry-run" ]; then
  echo
  echo "--- CHANGELOG.md would gain ---"
  printf '%s\n%s\n' "$ENTRY" "$LINK"
  exit 0
fi

# addon.xml carries version= on the root element and on every <import>, so match the
# line that opens the add-on rather than the attribute alone.
sed -i "s|\(<addon [^>]*id=\"service.fcast.receiver\"[^>]*version=\"\)[^\"]*|\1$VERSION|" "$ADDON_XML"
NEW=$(sed -n 's/.*<addon .*version="\([^"]*\)".*/\1/p' "$ADDON_XML")
[ "$NEW" = "$VERSION" ] || die "ERROR: addon.xml still says $NEW, the substitution missed"

# Inserted above the newest existing section. The entry reaches awk through ENVIRON
# rather than -v, because -v processes escape sequences and would rewrite a commit
# subject that happens to contain \t or \n.
ENTRY="$ENTRY" awk '
    !inserted && /^## \[/ { print ENVIRON["ENTRY"]; inserted = 1 }
    { print }
    END { if (!inserted) print ENVIRON["ENTRY"] }
' "$CHANGELOG" > "$CHANGELOG.tmp" && mv "$CHANGELOG.tmp" "$CHANGELOG"

if grep -qE '^\[[0-9][^]]*\]: ' "$CHANGELOG"; then
  # Above the newest existing definition, keeping the list in descending order.
  LINK="$LINK" awk '
      !inserted && /^\[[0-9][^]]*\]: / { print ENVIRON["LINK"]; inserted = 1 }
      { print }
  ' "$CHANGELOG" > "$CHANGELOG.tmp" && mv "$CHANGELOG.tmp" "$CHANGELOG"
else
  printf '\n%s\n' "$LINK" >> "$CHANGELOG"
fi

echo
echo "addon.xml and CHANGELOG.md updated. Still to do by hand:"
echo "  1. write this version's notes in CHANGELOG.md, above its '### Commits'"
echo "     heading. That section becomes the release body, so it is the release"
echo "     notes -- nothing else has to be written for them."
echo "  2. update <news> in addon.xml. That is what Kodi shows in the add-on's"
echo "     information dialog, and the release refuses to publish while it still"
echo "     holds the previous version's text."
echo "  3. commit, then: git tag $TAG && git push origin $TAG"
