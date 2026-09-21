<!-- The standing tail of every pre-release body. As dev/release-notes-tail.md, but for
     builds that go to testers: this version is NOT published to the add-on repository,
     so no installed Kodi is offered it as an update.

     @VERSION@ is substituted by prerelease.yml.
-->

## This is a test build

It is **not** published to the add-on repository, so no installed Kodi will offer it as an update. Install it by hand:

1. Download `service.fcast.receiver-@VERSION@.zip` below
2. **Settings > Add-ons > Install from zip file**, and pick it
3. Restart Kodi, so the service starts from the new code

The next repository release supersedes it on its own. This version carries a `~`, which Kodi sorts *before* the release it precedes, so the release will be offered as an update even though its number looks lower.

## If something is wrong with it

Open an issue at <https://github.com/ozankiratli/kodi-fcast-receiver/issues> with the version you are running and the log lines from `kodi.log`. Every line the add-on writes is prefixed `FCast Receiver`, so `grep 'FCast Receiver' kodi.log` is the whole of it.

The full history, with the commits behind each release, is in `CHANGELOG.md` in the repository.
