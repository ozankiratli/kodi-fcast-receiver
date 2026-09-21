#!/usr/bin/env bash
#
# Print one version's section of CHANGELOG.md on stdout.
#
# Usage: dev/scripts/changelog-section.sh <version> [--file <path>]
#
# The section runs from `## [<version>]` to whichever comes first: the next `## [`
# heading, the reference-link block at the foot of the file, or the end of the file.
# The `---` rule between sections belongs to neither, so it and the blank lines around
# it are trimmed. Everything else is printed as it stands, the `### Commits` list
# included.
#
# Exits non-zero with nothing on stdout when the version has no section, when the
# section is empty, or when it still carries the placeholder bump-version.sh leaves
# for the summary. Both release workflows run this twice: once as the gate on
# releasing, and once to write the release body. The same script both times, so the
# gate cannot pass a section the body would then fail to produce -- and a section
# nobody has written is one of the things it refuses, because that placeholder would
# otherwise be published as the release notes.
#
# A version here can carry '~' and '-' -- 0.9.9~pre, 0.2.1-beta -- so neither the
# heading nor the link definitions are matched as three numbers.

set -euo pipefail

VERSION=""
FILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --file)
            FILE="${2-}"
            [ -n "$FILE" ] || { echo "--file needs a path" >&2; exit 1; }
            shift
            ;;
        -*) echo "unknown option: $1" >&2; exit 1 ;;
        *)
            [ -z "$VERSION" ] || { echo "unexpected argument: $1" >&2; exit 1; }
            VERSION="$1"
            ;;
    esac
    shift
done

if [ -z "$VERSION" ]; then
    echo "Usage: $0 <version> [--file <path>]   (e.g. 1.0.0)" >&2
    exit 1
fi

[ -n "$FILE" ] || FILE="$(git rev-parse --show-toplevel)/CHANGELOG.md"
[ -f "$FILE" ] || { echo "changelog-section.sh: no such file: $FILE" >&2; exit 1; }

# The heading is matched as a literal rather than as a pattern: a version is full of
# dots, and `## [1.0.1]` as a regular expression also matches `## [1.0.10]`. The
# version reaches awk through ENVIRON rather than -v, which processes escape
# sequences in the value it is given.
VERSION="$VERSION" awk '
    BEGIN { want = "## [" ENVIRON["VERSION"] "]"; n = 0 }
    !inside && substr($0, 1, length(want)) == want { inside = 1 }
    inside && NR > 1 {
        # The next section, or the reference-link block that closes the file.
        if (substr($0, 1, 4) == "## [" && substr($0, 1, length(want)) != want) exit
        if ($0 ~ /^\[[0-9][^]]*\]: /) exit
    }
    inside && $0 ~ /^_Summary goes here/ { unwritten = 1 }
    inside { body[n++] = $0 }
    END {
        if (n == 0) exit 3
        if (unwritten) exit 5
        # The rule and the blank lines bounding one section from the next belong
        # to neither.
        last = n - 1
        while (last >= 0 && (body[last] == "" || body[last] == "---")) last--
        for (i = 0; i <= last; i++) print body[i]
        if (last < 0) exit 4
    }
' "$FILE" || {
    status=$?
    if [ "$status" -eq 3 ]; then
        echo "changelog-section.sh: ${FILE##*/} has no '## [$VERSION]' section" >&2
    elif [ "$status" -eq 4 ]; then
        echo "changelog-section.sh: the '## [$VERSION]' section is empty" >&2
    elif [ "$status" -eq 5 ]; then
        echo "changelog-section.sh: the '## [$VERSION]' section still holds the" >&2
        echo "  placeholder summary. That section is the release body, so write it" >&2
        echo "  above the '### Commits' heading before tagging." >&2
    fi
    exit 1
}
