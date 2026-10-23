# Asset Audit

Asset Audit is an Alliance Auth app for authorized auditors to audit assets
owned by authenticated characters across the server, regardless of alliance.
Auditors share scan history and persisted match results. It provides item type,
market-group, and solar-system filters, background scans, and progress reporting.

## Install

Install this package in the Alliance Auth environment:

```shell
python -m pip install .
```

To install the latest published release:

```shell
python -m pip install "https://github.com/LakMoore/aa-asset-audit/releases/latest/download/aa-asset-audit-latest.tar.gz"
```

The release workflow publishes this source archive under a stable asset name,
so the URL follows the latest GitHub Release. For reproducible installs, use
the versioned wheel attached to a specific release. To publish a release,
update `project.version` in `pyproject.toml` and push the matching tag, for
example `v0.1.0`. The workflow runs the tests, builds and validates both
distributions, and attaches them to the GitHub Release.

Add the app to `INSTALLED_APPS` in the Alliance Auth settings:

```python
"asset_audit.apps.AssetAuditConfig",
```

Add `"asset_audit"` to `APPS_WITH_PUBLIC_VIEWS`. The app URL hook uses this
allowlist only to bypass Alliance Auth's default main-character requirement;
the page itself still requires the `asset_audit.run_alliance_asset_audit`
permission.

Allow `publicData`, `esi-assets.read_assets.v1`,
`esi-characters.read_corporation_roles.v1`,
`esi-assets.read_corporation_assets.v1`,
`esi-contracts.read_character_contracts.v1`,
`esi-contracts.read_corporation_contracts.v1`,
`esi-markets.read_character_orders.v1`,
`esi-markets.read_corporation_orders.v1`, and
`esi-universe.read_structures.v1` for the application in the EVE Developer
Portal, and set Alliance Auth's login scopes to require them:

```python
LOGIN_TOKEN_SCOPES = [
	"publicData",
	"esi-assets.read_assets.v1",
	"esi-characters.read_corporation_roles.v1",
	"esi-assets.read_corporation_assets.v1",
	"esi-contracts.read_character_contracts.v1",
	"esi-contracts.read_corporation_contracts.v1",
	"esi-markets.read_character_orders.v1",
	"esi-markets.read_corporation_orders.v1",
	"esi-universe.read_structures.v1",
]
```

This is required: every user login requests asset access, and a Django system
checks fail if any required ESI scope is missing. Users with existing tokens
must authorize the expanded scopes on their next login. Corporation asset access
also requires the character to hold the Director role in that corporation.

Configure `ESI_USER_CONTACT_EMAIL` in Alliance Auth settings with an address
where the server maintainer can be reached. ESI requires this contact in the
request User-Agent.

Alliance Auth discovers the URL and menu hooks from `asset_audit.auth_hooks`.
Apply migrations with `python manage.py migrate`. At Django startup, Asset Audit
polls the Fuzzworks CSV files with `HEAD` requests and downloads only changed
versions. Fuzzworks does not publish `.md5sum` sidecars at these URLs, so the app
compares ETag/Last-Modified metadata and writes a local MD5 sidecar beside each
cached CSV under `BASE_DIR/.cache/asset_audit`. The cache directory can be changed
with `ASSET_AUDIT_REFERENCE_CACHE_DIR`; startup polling can be disabled with
`ASSET_AUDIT_POLL_REFERENCE_DATA_ON_STARTUP = False`.

Each dataset is parsed into the local database only when first queried or when
its cached CSV's MD5 differs from the loaded version. If the remote host is
unavailable, startup continues and existing cached data remains usable. The
solar-system, item-type, and market-group reference endpoints require the
`asset_audit.run_alliance_asset_audit` permission. Ensure the Alliance Auth
Celery worker is running for scans. Characters must belong to active users and
have a token with `esi-assets.read_assets.v1` for character assets. Corporation
assets additionally require a current Director role, plus tokens with
`esi-characters.read_corporation_roles.v1` and
`esi-assets.read_corporation_assets.v1`,
`esi-contracts.read_corporation_contracts.v1`, and
`esi-markets.read_corporation_orders.v1`.

Reference search endpoints accept a `q` query parameter with at least two
characters: `/asset-audit/reference/solar-systems/`,
`/asset-audit/reference/item-types/`, and
`/asset-audit/reference/market-groups/`.

The app page is available at `/asset-audit/`. It scans authenticated characters
across the server, regardless of alliance. Enter one or more item types and/or
market groups, plus one or more solar systems, from the typeahead suggestions.
Explicit item types and types within selected market groups (including child
groups) are combined with OR; the selected solar systems are then applied as an
ORed location filter. Asset locations resolve through their parent asset chain,
with station and structure names cached from ESI for filtering and result display.
Matching contract offers and open market orders are searched at their own
locations, and contract item details are cached independently per contract.
