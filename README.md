# haybarn-community-extensions

The community-extensions catalog for **[Haybarn](https://github.com/Query-farm-haybarn/haybarn)** — an independent derived distribution of DuckDB ("Haybarn, powered by DuckDB"), published by Query Farm LLC.

This repo mirrors [`duckdb/community-extensions`](https://github.com/duckdb/community-extensions) — every extension upstream lists gets rebuilt against the Haybarn engine, signed with the Haybarn trust root, and published to:

```
https://haybarn-extensions.query.farm/community/<duckdb_version>/<platform>/<name>.duckdb_extension.gz
```

A `haybarn` CLI on a supported platform can `INSTALL <name> FROM community; LOAD <name>;` and the engine fetches from that URL automatically.

## Status

🚧 **Bootstrapping** — the pipeline exists, the bucket is being provisioned, and the catalog is otherwise empty bar the `quack` smoke-test descriptor.

## How to add an extension

Same as upstream. Open a PR adding `extensions/<name>/description.yml`. The descriptor schema is **identical to upstream's**, with two optional Haybarn-specific fields for the rare extension that needs a patch:

```yaml
extension:
  name: my_extension                   # must equal containing directory name
  description: One-line summary
  version: 0.1.0
  language: C++
  build: cmake
  license: MIT
  maintainers:
    - your-gh-handle
  # (Optional, all identical-semantics to upstream:)
  # excluded_platforms:  "windows_amd64_mingw;linux_amd64_musl"
  # opt_in_platforms:    "windows_arm64"
  # requires_toolchains: "rust"
  # vcpkg_url:           https://github.com/your/vcpkg
  # vcpkg_commit:        <sha>
  # test_config:         <inline>

repo:
  github: owner/repo                   # upstream extension's repo
  ref: <commit-sha>                    # pinned commit

  # OPTIONAL Haybarn escape hatches (use only if the upstream code doesn't
  # build against Haybarn's curated patch stack):
  # haybarn_fork: Query-farm-haybarn/your-fork
  # haybarn_ref:  <commit-sha>
  # When haybarn_fork + haybarn_ref are both set, the build uses them
  # instead of repo.github + repo.ref.

docs:
  hello_world: |
    SELECT 1;
  extended_description: |
    Markdown description shown in the catalog.
```

On PR merge, `.github/workflows/build.yml` parses the descriptor, kicks off the [`haybarn-extension-ci-tools`](https://github.com/Query-farm-haybarn/haybarn-extension-ci-tools) reusable workflow to build for all 12 supported platforms, signs each artifact with the Haybarn key, and uploads to the R2 bucket.

## Where descriptors come from

- **Most** descriptors: auto-imported from upstream by [`sync_from_upstream.yml`](.github/workflows/sync_from_upstream.yml) (weekly cron + on-demand). The bot opens a PR; a human reviews and merges.
- **Haybarn-only descriptors**: if we maintain a descriptor with no upstream counterpart (e.g. a Haybarn-specific extension) it stays in `extensions/` and the sync script leaves it alone.
- The `repo.andium` field (DuckDB v1.4.4 escape hatch) is silently stripped on import — Haybarn is tagged off DuckDB v1.5.2 and v1.4 doesn't apply.

## Signing and trust

Both `core` (`haybarn-extensions.query.farm/core`) and `community` (`haybarn-extensions.query.farm/community`) are signed with the **same Haybarn RSA key** and served from the same R2 bucket under distinct top-level path prefixes. One cryptographic identity, one origin, two distribution channels. The corresponding public key is baked into the Haybarn engine at `src/main/extension/extension_helper.cpp` as `HAYBARN_TRUST_ROOT` and verified for every `INSTALL`.

Anything signed with a DuckDB key — core OR community — will be rejected by Haybarn. This is intentional. Haybarn ABI-compatible doesn't mean security-state-compatible.

## Secrets required

Org-level secrets on `Query-farm-haybarn`, all reused from the existing core-extensions pipeline:

| Secret | Purpose |
|---|---|
| `HAYBARN_VCPKG_TOKEN` | Bearer token for the Cloudflare Worker fronting the R2 vcpkg + ccache bucket. Same token works against both the core and community buckets. |
| `HAYBARN_EXTENSION_SIGNING_PK` | RSA private key (PEM). The cryptographic root. Signs both core and community extensions. |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_ENDPOINT` | R2 access for both `haybarn-extensions` and `haybarn-community-extensions` buckets (one key, two buckets — granted via the Cloudflare dashboard). Same names as `haybarn-extensions.yml`. |

No CDN-purge token is needed: every artifact lives at a unique URL (version + git-sha in the path), so new deploys land at new URLs rather than overwriting cached ones. Cloudflare's edge holds onto the old binaries until they age out — there's nothing left referencing them.

## License

MIT — see [LICENSE](LICENSE). Individual extensions carry their own licenses; the LICENSE in this repo covers only the build orchestration code (workflows, scripts).
