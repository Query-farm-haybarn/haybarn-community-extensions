# Descriptor parser for the Haybarn community-extensions build pipeline.
#
# Reads the descriptor at the path in ALL_CHANGED_FILES, validates it, and
# emits env.sh with COMMUNITY_EXTENSION_* values that .github/workflows/build.yml
# picks up as job outputs.
#
# Mirrors upstream's scripts/build.py with two Haybarn-specific changes:
#   1. The `repo.andium` field is silently ignored. Upstream uses it as a
#      DuckDB v1.4.4 escape hatch (codename "Andium"). Haybarn is tagged off
#      DuckDB v1.5.2 — that escape hatch never applies here.
#   2. The `ref_next` field is still honored: when DUCKDB_VERSION points at a
#      pre-release that hasn't become DUCKDB_LATEST_STABLE yet (e.g. building
#      against a `haybarn-v1.6-betaN` ahead of the Haybarn v1.6 cut),
#      `ref_next` is used to pick a forward-compatible extension commit. In
#      steady state both vars are `v1.5.2` and `ref` is used.

import os
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
    hdl.write(f"COMMUNITY_EXTENSION_GITHUB={desc['repo']['github']}\n")
    if 'canonical_name' in desc.get('repo', {}):
        hdl.write(f"COMMUNITY_EXTENSION_CANONICAL_NAME={desc['repo']['canonical_name']}\n")

    extension_ref = desc['repo']['ref']
    # Haybarn: `andium` (DuckDB v1.4.4 escape hatch) is intentionally not
    # consulted — see module docstring. We only honor `ref_next` for the
    # pre-release case.
    duckdb_version = os.environ.get('DUCKDB_VERSION', 'v1.5.2')
    duckdb_latest_stable = os.environ.get('DUCKDB_LATEST_STABLE', 'v1.5.2')
    if duckdb_version != duckdb_latest_stable:
        if 'ref_next' in desc['repo']:
            extension_ref = desc['repo']['ref_next']
            print(f"Using ref_next={extension_ref} (DUCKDB_VERSION={duckdb_version}, "
                  f"latest stable={duckdb_latest_stable})")

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
    # etc. Strip semicolons and empty entries, then categorize.
    toolchain_set = {
        t for t in (requires_toolchains or '').split(';') if t
    }
    if not toolchain_set:
        image_variant = 'base'
    elif toolchain_set == {'rust'}:
        image_variant = 'rust'
    else:
        image_variant = 'full'
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
