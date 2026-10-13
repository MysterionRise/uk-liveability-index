# Data releases

The data pack ships separately from the code, as a monthly GitHub pre-release tagged
`data-YYYY-MM` (`.n` for a second one in the same month). Each carries
`lix-serve.tar.gz`, its checksum and the build's `manifest.json`, and its notes list every
source's data period and edition and what changed since the previous release. A code
release reads the newest pack with the serve schema it understands:
`make data-download` finds it (`DATA_TAG=...` picks one), and `make quickstart` calls
that. Cutting one: `make refresh`, commit the lockfile, `make data-release`
([scripts/data-release.sh](../scripts/data-release.sh)).

| Release | Built | Data to | Serve schema |
|---|---|---|---|
| [data-2026-10](https://github.com/MysterionRise/uk-liveability-index/releases/tag/data-2026-10) | 2026-10-11 | 2026-10-11 | 2 |
| [data-2026-10.1](https://github.com/MysterionRise/uk-liveability-index/releases/tag/data-2026-10.1) | 2026-10-13 | 2026-10-13 | 3 |
| [data-2026-10.2](https://github.com/MysterionRise/uk-liveability-index/releases/tag/data-2026-10.2) | 2026-10-13 | 2026-10-13 | 3 |
