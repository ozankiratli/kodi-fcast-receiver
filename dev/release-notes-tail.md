<!-- The standing tail of every release body: the part that does not change from
     release to release. The part that does is this version's CHANGELOG.md section,
     which dev/scripts/changelog-section.sh extracts and release.yml puts above this.

     @VERSION@ is substituted by the workflow. This lives in a file rather than inside
     the workflow because it is markdown full of backticks and fenced blocks, which a
     YAML block scalar and a shell heredoc each mangle in their own way.
-->

## Getting it

If you installed the add-on from the repository, Kodi offers this update on its own, as long as **Settings > System > Add-ons > Updates** is set to *Install updates automatically*.

If you have not installed the repository yet, that is the way to keep up to date without doing anything:

1. Download `repository.fcast.ozankiratli-1.0.0.zip` from <https://ozankiratli.github.io/kodi-fcast-receiver/>
2. **Settings > Add-ons > Install from zip file**, and pick it
3. **Settings > Add-ons > Install from repository > FCast Receiver Repository > Services > FCast Receiver**

Already running a zip install? Installing the repository alongside it is enough -- there is no need to uninstall the add-on first.

## Or install this build by hand

`service.fcast.receiver-@VERSION@.zip` below is the add-on. **Settings > Add-ons > Install from zip file**. Updates are then manual.

## After updating

Kodi loads a service add-on at start-up. If the receiver does not appear to your sender after the update, restart Kodi.

The full history, with the commits behind each release, is in `CHANGELOG.md` in the repository.
