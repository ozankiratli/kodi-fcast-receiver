# Releasing

## Versions, and why the tilde matters

Kodi compares add-on versions Debian-style. Everything after a `-` is a revision, and a revision sorts **above** no revision, so `1.0.0-beta1` is *newer* than `1.0.0` as far as Kodi is concerned, and nobody running that beta would ever be offered the release. `~` is the one character that sorts below empty.

So a pre-release is written `1.0.0~beta1` in `addon.xml`. A git ref cannot contain `~`, so the tag spells it with a `-`: `p1.0.0-beta1`. The two workflows both re-derive the expected version from the tag and refuse to build if `addon.xml` disagrees, which is the guard against tagging the wrong commit.

`addon.xml` is the only place the version is written down. The Makefile reads it, the zip is named from it, and CI checks it against the tag.

## Opening a release

    dev/scripts/bump-version.sh 1.0.0 --dry-run
    dev/scripts/bump-version.sh 1.0.0

It sets the version in `addon.xml` and puts a new section at the top of `CHANGELOG.md` listing every commit since the last tag. It does not commit, tag or push anything, and it prints what is still yours to do:

1. Write the summary in `CHANGELOG.md`, replacing the `_Summary goes here._` placeholder above the commit list. The commits stay underneath so anyone reading can see how it got here.
2. Update `<news>` in `addon.xml`. **That is what Kodi shows in the add-on's information dialog, not `CHANGELOG.md`.** Forgetting it means the release announces the previous one.
3. Commit, then tag and push.

The script picks the tag from the version: a version containing `~` becomes a `p` tag and goes to testers, anything else becomes a `v` tag and goes to everyone.

## What each tag does

`v*` triggers `release.yml`: runs the tests, checks the tag against `addon.xml`, builds the add-on, the static Kodi repository and the landing page, publishes all of it to GitHub Pages, and attaches the zip to a GitHub release. **This is the one that reaches installed devices.** Kodi offers an update when the published version is higher than the installed one, so the bump is what makes devices pick it up.

`p*` and `*-pre` trigger `prerelease.yml`: runs the tests, checks the version, builds the zip and attaches it to a GitHub prerelease. It touches neither Pages nor the add-on repository, so no installed Kodi is ever offered it. Testers install the zip by hand and get the real release later through the repository, which supersedes the `~` version on its own.

A push to `main` touching `site/**` triggers `pages.yml`, which republishes the website. It rebuilds the add-on repository from the newest `v*` tag rather than from `main`, so editing the page can never publish an unreleased add-on to devices.

Note that the `v*` glob in `release.yml` would also match `v1.0.0-beta1` and publish that beta to every device. That is exactly what `bump-version.sh` steers around by giving `~` versions a `p` tag instead.

## The Pages coupling

The landing page and the add-on repository are one GitHub Pages site, and a deployment replaces the whole of it. Publishing either on its own takes the other down, and a missing `addons.xml` stops every installed device from seeing updates. That is why `make pages` builds both, why `pages.yml` rebuilds the repository even though only the page changed, and why both workflows share the `pages` concurrency group so a release and a site edit cannot race.

## A pre-release, step by step

    dev/scripts/bump-version.sh 1.0.0~beta1
    # write the CHANGELOG summary and the <news> element
    make test
    git add -A && git commit -m "Release 1.0.0~beta1"
    git tag p1.0.0-beta1
    git push origin main
    git push origin p1.0.0-beta1

Then point testers at the GitHub release page for the zip.

## A release, step by step

    dev/scripts/bump-version.sh 1.0.0
    # write the CHANGELOG summary and the <news> element
    make test
    git add -A && git commit -m "Release 1.0.0"
    git tag v1.0.0
    git push origin main
    git push origin v1.0.0

Watch the Publish add-on repository workflow. When it is green, the Pages site carries the new `addons.xml` and zip, and devices pick the update up on their own schedule -- as long as Settings > System > Add-ons > Updates is set to *Install updates automatically*.

Check it from a device, or with curl against `addons.xml`, before telling anyone. A green workflow means the artifact was published, not that Kodi liked it.

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
