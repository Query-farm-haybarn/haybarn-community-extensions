# Descriptor parser for the Haybarn community-extensions build pipeline.
#
# Reads the descriptor at the path in ALL_CHANGED_FILES, validates it, and
# emits env.sh with COMMUNITY_EXTENSION_* values that .github/workflows/build.yml
# picks up as job outputs.
#
# Ref selection follows upstream's scripts/build.py exactly (one descriptor
# set, one field per engine line):
#   * `repo.ref`       — the build for DUCKDB_LATEST_STABLE (v2.0.0)
#   * `repo.variegata` — the build for v1.5.6, when the descriptor has one
#   * `repo.andium`    — the build for v1.4.5 (never used by Haybarn)
#   * `repo.ref_next`  — any other (pre-release) engine; with
#     COMMUNITY_EXTENSION_REQUIRE_REF_NEXT=true a descriptor without one is
#     skipped (no COMMUNITY_EXTENSION_REF is written, build.yml skips the build)
#
# Haybarn differences:
#   1. DUCKDB_VERSION may be a Haybarn engine tag (`haybarn-v2.0.0-rc6`); it
#      is normalized to the plain semver (`v2.0.0`) before the comparison, so
#      the same tag-shaped inputs that pick the engine also pick the ref.
#   2. `repo.haybarn_fork` / `repo.haybarn_ref` override `github` / `ref`
#      (the latest-stable build), and `repo.haybarn_variegata` overrides
#      `variegata`. The sync keeps these Haybarn-only fields, so a Haybarn
#      patch or a Haybarn-side ref survives later upstream syncs.
#   3. Extra outputs for the Haybarn deploy (license, description, source
#      repo), `haybarn_skip_tests`, and toolchain normalization.

import os
import re
import sys

import yaml

if 'ALL_CHANGED_FILES' in os.environ:
    desc_files = os.environ['ALL_CHANGED_FILES'].split(' ')
else:
    desc_files = []

print(f"Files changed: {desc_files}")

if len(desc_files) > 1:
    raise ValueError(
        'cannot have multiple descriptors changed or packages with spaces in their names'
    )

deploy = True

if len(desc_files) == 0 or len(desc_files[0]) == 0:
    # Haybarn: upstream's fallback was 'extensions/quack/description.yml',
    # but we renamed our smoke test to waddle to match the actual CMake
    # output name of duckdb/extension-template@main (see commit history).
    print("No changed files, only waddle will be built as a test")
    desc_files = ['extensions/waddle/description.yml']
    deploy = False

desc_file = desc_files[0]

with open(desc_file, 'r') as stream:
    desc = yaml.safe_load(stream)

print(desc)

# Validate that the directory name matches the extension name
dir_name = os.path.basename(os.path.dirname(desc_file))
extension_name = desc['extension']['name']
if dir_name != extension_name:
    raise ValueError(
        f"Directory name '{dir_name}' does not match extension name "
        f"'{extension_name}' in {desc_file}. Please rename the directory "
        f"to '{extension_name}'."
    )

with open('env.sh', 'w+') as hdl:
    repo = desc['repo']
    # Haybarn: `haybarn_fork` builds a Haybarn-patched copy of the source.
    hdl.write(f"COMMUNITY_EXTENSION_GITHUB={repo.get('haybarn_fork') or repo['github']}\n")
    if 'canonical_name' in repo:
        hdl.write(f"COMMUNITY_EXTENSION_CANONICAL_NAME={repo['canonical_name']}\n")

    # Haybarn: compare the plain engine semver, not the Haybarn tag.
    duckdb_version = re.sub(r'^haybarn-(v\d+\.\d+\.\d+).*$', r'\1', os.environ['DUCKDB_VERSION'])
    duckdb_latest_stable = re.sub(r'^haybarn-(v\d+\.\d+\.\d+).*$', r'\1', os.environ['DUCKDB_LATEST_STABLE'])

    # Upstream's selection, verbatim apart from the Haybarn overrides.
    extension_ref = repo.get('haybarn_ref') or repo['ref']
    if duckdb_version == 'v1.4.5':
        if 'andium' in repo:
            extension_ref = repo['andium']
    elif duckdb_version == 'v1.5.6':
        if 'haybarn_variegata' in repo:
            extension_ref = repo['haybarn_variegata']
        elif 'variegata' in repo:
            extension_ref = repo['variegata']
    elif duckdb_version != duckdb_latest_stable:
        if repo.get('ref_next'):
            extension_ref = repo['ref_next']
        elif os.environ.get('COMMUNITY_EXTENSION_REQUIRE_REF_NEXT') == 'true':
            print("Skipping prerelease validation: repo.ref_next is not defined")
            extension_ref = None
    print(f"DUCKDB_VERSION={duckdb_version} (latest stable {duckdb_latest_stable}) -> ref {extension_ref}")
    if extension_ref:
        hdl.write(f"COMMUNITY_EXTENSION_REF={extension_ref}\n")
    hdl.write(f"COMMUNITY_EXTENSION_NAME={desc['extension']['name']}\n")
    # Haybarn: pass the upstream extension's license through to the deploy
    # workflow so the published npm package.json / PyPI metadata reflects
    # the extension's actual license rather than misattributing it as MIT
    # (which is what the engine + core extensions are, and was the previous
    # hardcoded fallback in npm_build_leaf.py / npm_build_meta.py).
    license_value = desc['extension'].get('license')
    if license_value:
        hdl.write(f"COMMUNITY_EXTENSION_LICENSE={license_value}\n")

    # Description + upstream source repo URL — used by npm_build_meta.py /
    # npm_build_leaf.py to render the generated README.md with a real
    # extension description and a link back to the upstream source repo.
    # GITHUB_OUTPUT uses `name=value` lines, so squash any newlines in the
    # description to a single space (the existing test_config block does
    # the same for the same reason).
    description_text = (desc['extension'].get('description') or '').strip()
    if description_text:
        single_line = ' '.join(description_text.split())
        hdl.write(f"COMMUNITY_EXTENSION_DESCRIPTION={single_line}\n")
    ext_repo_github = (desc.get('repo', {}).get('github') or '').strip()
    if ext_repo_github:
        hdl.write(f"COMMUNITY_EXTENSION_SOURCE_REPO=https://github.com/{ext_repo_github}\n")

    # Haybarn-specific: `haybarn_skip_tests: true` in the descriptor skips
    # the per-extension test suite during the build. We use this for
    # extensions whose binaries build cleanly but whose test data is
    # environment-sensitive in ways that don't survive the Haybarn CI
    # context (e.g. h5db's cert-chain test, magic's remote-URL fetch).
    # The build leg still runs end-to-end and produces a valid signed
    # binary; only the test step is bypassed.
    if desc['extension'].get('haybarn_skip_tests') is True:
        hdl.write("COMMUNITY_EXTENSION_SKIP_TESTS=true\n")
    # Haybarn-specific: `haybarn_job_timeout_minutes: <n>` raises ci-tools'
    # 45-minute per-platform job limit for extensions whose cold build is
    # longer (grpc, arrow and google-cloud from vcpkg source).
    job_timeout = desc['extension'].get('haybarn_job_timeout_minutes')
    if isinstance(job_timeout, int) and not isinstance(job_timeout, bool) and job_timeout > 0:
        hdl.write(f"COMMUNITY_EXTENSION_JOB_TIMEOUT_MINUTES={job_timeout}\n")
    # Haybarn-specific: `haybarn_wasm_cmake_flags: "<flags>"` passes extra CMake
    # flags to the wasm legs only (e.g. to drop an extension's pin of Rust 1.86,
    # which it keeps for upstream's older emscripten).
    wasm_cmake_flags = desc['extension'].get('haybarn_wasm_cmake_flags')
    if isinstance(wasm_cmake_flags, str) and wasm_cmake_flags.strip():
        hdl.write(f"COMMUNITY_EXTENSION_WASM_CMAKE_FLAGS={' '.join(wasm_cmake_flags.split())}\n")
    excluded_platforms     = desc['extension'].get('excluded_platforms')
    opt_in_platforms       = desc['extension'].get('opt_in_platforms')
    requires_toolchains    = desc['extension'].get('requires_toolchains')
    custom_toolchain_script = desc['extension'].get('custom_toolchain_script')
    vcpkg_url              = desc['extension'].get('vcpkg_url')
    vcpkg_commit           = desc['extension'].get('vcpkg_commit')
    test_config            = desc['extension'].get('test_config')

    # Haybarn: derive which pre-built Linux build image to pull from GHCR
    # based on this extension's toolchain needs. Three variants are published:
    #   base — no extras
    #   rust — base + Rust
    #   full — base + Rust + Go + Fortran + parser_tools + unixodbc + multimedia
    # See publish-build-images.yml in haybarn-extension-ci-tools.
    #
    # Format of requires_toolchains varies: "rust", ";rust;", ";rust;parser_tools;",
    # etc. Some upstream descriptors (cloudfs, valhalla_routing) instead use a
    # YAML *list*: `requires_toolchains: [cmake, curl, ...]`. Upstream's build.py
    # never splits this field so it tolerates both shapes by accident; ours has
    # to parse it to pick the GHCR image variant, so normalize first — otherwise
    # the list form dies with "'list' object has no attribute 'split'" in
    # prepare, before any build starts.
    if isinstance(requires_toolchains, (list, tuple)):
        toolchain_set = {str(t).strip() for t in requires_toolchains if str(t).strip()}
    else:
        toolchain_set = {
            t.strip() for t in (requires_toolchains or '').split(';') if t.strip()
        }
    # `rust_nightly` (Haybarn-only): the extension needs the pinned nightly +
    # rust-src baked into the `rust-nightly` wasm image (wasm_threads Rust code
    # rebuilds std with `-Z build-std` for atomics). Native legs treat it as
    # plain `rust`, so it also implies the `rust` token downstream.
    wasm_rust_nightly = 'rust_nightly' in toolchain_set
    if wasm_rust_nightly:
        toolchain_set = (toolchain_set - {'rust_nightly'}) | {'rust'}
    # Re-emit in the canonical ';'-separated form the downstream reusable
    # workflow expects; writing a Python list repr would hand it garbage.
    requires_toolchains = ';'.join(sorted(toolchain_set))
    if not toolchain_set:
        image_variant = 'base'
    elif toolchain_set == {'rust'}:
        image_variant = 'rust'
    else:
        image_variant = 'full'
    # Wasm images come in base / rust / rust-nightly only.
    if wasm_rust_nightly:
        wasm_image_variant = 'rust-nightly'
    elif image_variant == 'base':
        wasm_image_variant = 'base'
    else:
        wasm_image_variant = 'rust'
    # Same list-vs-string split for the platform fields: mssql and
    # mssql_ducklake carry `excluded_platforms: [osx_amd64, ...]` upstream.
    # These land in build.yml's exclude_archs/opt_in_archs, which expect the
    # ';'-separated form — a Python list repr matches no platform name, so
    # every "excluded" platform would quietly build anyway.
    if isinstance(excluded_platforms, (list, tuple)):
        excluded_platforms = ';'.join(
            str(p).strip() for p in excluded_platforms if str(p).strip()
        )
    if isinstance(opt_in_platforms, (list, tuple)):
        opt_in_platforms = ';'.join(
            str(p).strip() for p in opt_in_platforms if str(p).strip()
        )
    if excluded_platforms:
        hdl.write(f"COMMUNITY_EXTENSION_EXCLUDE_PLATFORMS={excluded_platforms}\n")
    if opt_in_platforms:
        hdl.write(f"COMMUNITY_EXTENSION_OPT_IN_PLATFORMS={opt_in_platforms}\n")
    if requires_toolchains:
        hdl.write(f"COMMUNITY_EXTENSION_REQUIRES_TOOLCHAINS={requires_toolchains}\n")
    if vcpkg_url:
        hdl.write(f"COMMUNITY_EXTENSION_VCPKG_URL={vcpkg_url}\n")
    if vcpkg_commit:
        hdl.write(f"COMMUNITY_EXTENSION_VCPKG_COMMIT={vcpkg_commit}\n")
    if deploy:
        hdl.write("COMMUNITY_EXTENSION_DEPLOY=1\n")
    if test_config:
        escaped_config = test_config.replace("\n", "")
        hdl.write(f"COMMUNITY_EXTENSION_TEST_CONFIG={escaped_config}\n")
    hdl.write(f"COMMUNITY_EXTENSION_IMAGE_VARIANT={image_variant}\n")
    hdl.write(f"COMMUNITY_EXTENSION_WASM_IMAGE_VARIANT={wasm_image_variant}\n")
