#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

LC=$(printf 'mini%s' 'claw')
PC=$(printf 'Mini%s' 'Claw')
UC=$(printf 'MINI%s' 'CLAW')

count=0

while IFS= read -r file; do
    [[ -f "$file" ]] || continue
    charset=$(file --mime "$file" | grep -o 'charset=[^ ]*' | cut -d= -f2)
    [[ "$charset" == "binary" ]] && continue

    before=$(md5sum "$file")
    sed -i \
        -e "s/${PC}/Kaizen/g" \
        -e "s/${LC}/kaizen/g" \
        -e "s/${UC}/KAIZEN/g" \
        "$file"
    after=$(md5sum "$file")

    [[ "$before" != "$after" ]] && (( count++ )) || true
done < <(git ls-files)

echo "Rewrote ${count} file(s)."
