# Releasing

## Versions, and why the tilde matters

Kodi compares add-on versions Debian-style. Everything after a `-` is a revision, and a revision sorts **above** no revision, so `1.0.0-beta1` is *newer* than `1.0.0` as far as Kodi is concerned, and nobody running that beta would ever be offered the release. `~` is the one character that sorts below empty.

So a pre-release is written `1.0.0~beta1` in `addon.xml`. A git ref cannot contain `~`, so the tag spells it with a `-`: `p1.0.0-beta1`. The two workflows both re-derive the expected version from the tag and refuse to build if `addon.xml` disagrees, which is the guard against tagging the wrong commit.

`addon.xml` is the only place the version is written down. The Makefile reads it, the zip is named from it, and CI checks it against the tag.

## Opening a release

    dev/scripts/bump-version.sh 1.0.0 --dry-run
    dev/scripts/bump-version.sh 1.0.0

It sets the version in `addon.xml` and prepends a section to `CHANGELOG.md` holding every commit since the last tag under a `### Commits` heading, with the matching reference-link definition at the foot of the file. It does not commit, tag or push anything.

Two things are then yours to write, and each has something that refuses to publish without it:

1. **This version's prose in `CHANGELOG.md`, above the `### Commits` heading** -- not over it, because the commit list stays as the record of what actually landed. That section *is* the release notes; see below.
2. **`<news>` in `addon.xml`.** That is what Kodi shows in the add-on's information dialog, and it is the only release note most people ever see: the changelog is not shipped inside the add-on and the GitHub release body is not reachable from Kodi. It has to be written by hand, because forty words for that dialog are not an extract of anything.

The script picks the tag from the version: a version containing `~` becomes a `p` tag and goes to testers, anything else becomes a `v` tag and goes to everyone.

## Where the release notes come from

Nothing is written twice and nothing is copied by hand. `dev/scripts/changelog-section.sh <version>` prints one version's section of `CHANGELOG.md`, and both release workflows run that exact command and publish its output as the body of the GitHub release, followed by a standing tail -- `dev/release-notes-tail.md` for a release, `dev/release-notes-tail-prerelease.md` for a test build -- with `@VERSION@` substituted. The tails are the part that does not change from release to release: how to install it, and what to do afterwards.

Each workflow runs the extractor twice: once as a gate before building anything, once to write the body. The same script both times, so the gate cannot pass a section the body step would then fail to produce. It refuses three things: a version with no section, an empty section, and a section still carrying the `_Summary goes here._` placeholder, which would otherwise be published as the release notes.

`dev/scripts/check-news.sh` is the other gate. It compares `<news>` against the same element at the previous tag, and unchanged means nobody wrote one for this version. It is a hard failure in `release.yml`, which reaches every installed device, and a warning in `prerelease.yml`, where tagging `rc2` an hour after `rc1` should not require rewriting the information dialog in between.

Both scripts run locally, which is the point of them being scripts rather than steps buried in a workflow. Run either before you tag and you know what CI will say -- see "Seeing it before you tag" below.

## What each tag does

`v*` triggers `release.yml`: runs the tests, checks the tag against `addon.xml`, puts the changelog and `<news>` gates in front of everything else, builds the add-on, the static Kodi repository and the landing page, publishes all of it to GitHub Pages, and attaches the zip to a GitHub release with this version's changelog section as its body. **This is the one that reaches installed devices.** Kodi offers an update when the published version is higher than the installed one, so the bump is what makes devices pick it up.

`p*` and `*-pre` trigger `prerelease.yml`: runs the tests, checks the version, builds the zip and attaches it to a GitHub prerelease. It touches neither Pages nor the add-on repository, so no installed Kodi is ever offered it. Testers install the zip by hand and get the real release later through the repository, which supersedes the `~` version on its own.

A push to `main` touching `site/**` triggers `pages.yml`, which republishes the website. It rebuilds the add-on repository from the newest `v*` tag rather than from `main`, so editing the page can never publish an unreleased add-on to devices.

Note that the `v*` glob in `release.yml` would also match `v1.0.0-beta1` and publish that beta to every device. That is exactly what `bump-version.sh` steers around by giving `~` versions a `p` tag instead.

## The Pages coupling

The landing page and the add-on repository are one GitHub Pages site, and a deployment replaces the whole of it. Publishing either on its own takes the other down, and a missing `addons.xml` stops every installed device from seeing updates. That is why `make pages` builds both, why `pages.yml` rebuilds the repository even though only the page changed, and why both workflows share the `pages` concurrency group so a release and a site edit cannot race.

## A pre-release, step by step

    dev/scripts/bump-version.sh 1.0.0~beta1
    # write this version's prose into CHANGELOG.md, above '### Commits'
    # write <news> in addon.xml
    make test
    git add -A && git commit -m "Release 1.0.0~beta1"
    git tag p1.0.0-beta1
    git push origin main
    git push origin p1.0.0-beta1

Then point testers at the GitHub release page for the zip.

## A release, step by step

    dev/scripts/bump-version.sh 1.0.0
    # write this version's prose into CHANGELOG.md, above '### Commits'
    # write <news> in addon.xml
    make test
    git add -A && git commit -m "Release 1.0.0"
    git tag v1.0.0
    git push origin main
    git push origin refs/tags/v1.0.0

**Spell the tag out as `refs/tags/`** when a branch shares its name. This repository has a branch called `v1.0.0` as well as the tag, and `git push origin v1.0.0` then fails with *src refspec v1.0.0 matches more than one* -- git will not guess which of the two you meant. `refs/tags/v1.0.0` is never ambiguous, so it is the safer form to use always.

**The release notes are not a step, because nothing has to be done to them.** The changelog section written above *is* the release body. Pushing the tag is what publishes it.

Then watch the Publish add-on repository workflow. When it is green, the Pages site carries the new `addons.xml` and zip, and devices pick the update up on their own schedule -- as long as Settings > System > Add-ons > Updates is set to *Install updates automatically*.

Check it from a device, or with curl against `addons.xml`, before telling anyone. A green workflow means the artifact was published, not that Kodi liked it.

## Seeing it before you tag

Optional, and nothing here is copied anywhere. These are the same two commands the workflow runs, so running them yourself is how you find out now rather than from a failed run:

    dev/scripts/changelog-section.sh 1.0.0   # the release body, above the standing tail
    dev/scripts/check-news.sh                # the gate on <news>

`workflow_dispatch` on `release.yml` is the same idea from the other end: it builds and checks everything and creates no release, so a run before the tag exists proves the release would work.

## Building the pieces by hand

    make            # the add-on zip, into dist/
    make repo       # the static Kodi repository, into repo/
    make site       # the landing page, into the same tree
    make pages      # repo then site, which is what CI uploads

`make repo` and `make pages` take `REPO_URL`, which is baked into the repository add-on and has to be the URL the tree will actually be served from. CI passes the Pages base URL; the default is the project's own Pages address, which is right for a local check of the output.

## Mirroring to forgejo

The `v1.0.0` branch is the one that exists on the forgejo remote, and it trails `main`.

    git fetch forgejo
    git switch v1.0.0
    git merge --ff-only main
    git push forgejo v1.0.0
    git switch main

Name the remote. `branch.v1.0.0.pushremote` is `origin`, so a bare `git push` from that branch goes to GitHub instead. Keep `--ff-only` so a forgejo that has moved makes the merge refuse rather than quietly making a merge commit.
