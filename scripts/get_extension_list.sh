#!/usr/bin/env bash
# Enumerate every extension under `extensions/` into a JSON-array literal
# for the build_all.yml matrix. Quoted with single quotes per upstream's
# convention (the workflow expects this exact shape).

set -eo pipefail

echo -n "EXTENSION_LIST=[" > extension_list
for extension_folder in extensions/*; do
    extension_name=$(basename -- "$extension_folder")
    echo -n "'$extension_name'," >> extension_list
done
echo "]" >> extension_list
