#!/usr/bin/env bash
# AI-generated.
set -u

cd "$(dirname "$0")"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

for src in tests/ok/*.txt tests/err/*.txt; do
    base=${src%.txt}
    name=${base#tests/}
    ll="$TMP/${name//\//_}.ll"

    if [[ $name == ok/* ]]; then
        if ! python3 compiler.py "$src" "$ll" 2>"$TMP/err"; then
            echo "FAIL $name: compiler failed"
            sed 's/^/      /' "$TMP/err"
            fail=$((fail + 1)); continue
        fi
        if ! actual=$(lli "$ll" 2>"$TMP/err"); then
            echo "FAIL $name: lli failed"
            sed 's/^/      /' "$TMP/err"
            fail=$((fail + 1)); continue
        fi
        if [[ -e $base.ast ]] && ! python3 compiler.py --ast "$src" | diff -q - "$base.ast" >/dev/null; then
            echo "FAIL $name: --ast does not match $base.ast"
            python3 compiler.py --ast "$src" | diff - "$base.ast" | sed 's/^/      /'
            fail=$((fail + 1)); continue
        fi
    else
        if python3 compiler.py "$src" "$ll" 2>"$TMP/err"; then
            echo "FAIL $name: expected a compilation error"
            fail=$((fail + 1)); continue
        fi
        if [[ -e $ll ]]; then
            echo "FAIL $name: output file written despite the error"
            fail=$((fail + 1)); continue
        fi
        actual=$(cat "$TMP/err")
    fi

    if [[ $actual == "$(cat "$base.expected")" ]]; then
        echo "ok   $name"
        pass=$((pass + 1))
    else
        echo "FAIL $name"
        echo "      expected: $(cat "$base.expected")"
        echo "      actual:   $actual"
        fail=$((fail + 1))
    fi
done

echo
echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
