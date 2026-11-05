#!/usr/bin/env bash
# AI-generated.
set -u
shopt -s nullglob

cd "$(dirname "$0")"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

report() {
    if [[ -z $2 ]]; then
        echo "ok   $1"
        pass=$((pass + 1))
    else
        echo "FAIL $1: $2"
        fail=$((fail + 1))
    fi
}

compare_ast() {
    local actual
    actual=$(python3 compiler.py "$@" 2>&1)
    [[ $actual == "$(cat "$AST")" ]] || { echo "--ast does not match $AST"; diff <(echo "$actual") "$AST" | sed 's/^/      /' >&2; }
}

run_ok() {
    local src=$1 input=$2 expected=$3 flag=$4 ll="$TMP/out.ll" code=0 want=0
    rm -f "$ll"
    if ! python3 compiler.py $flag "$src" "$ll" 2>"$TMP/err"; then
        echo "compiler failed: $(tail -1 "$TMP/err")"
        return
    fi
    lli "$ll" <"$input" >"$TMP/out" 2>"$TMP/err" || code=$?
    [[ -e ${expected%.expected}.code ]] && want=$(cat "${expected%.expected}.code")
    if [[ $code != "$want" ]]; then
        echo "exit code $code, expected $want $(head -1 "$TMP/err")"
    elif [[ $(cat "$TMP/out") != "$(cat "$expected")" ]]; then
        echo "stdout differs: $(diff "$TMP/out" "$expected" | head -3 | tr '\n' ' ')"
    fi
}

run_err() {
    local src=$1 flag=$2 ll="$TMP/out.ll"
    rm -f "$ll"
    if python3 compiler.py $flag "$src" "$ll" 2>"$TMP/err"; then
        echo "expected a compilation error"
    elif [[ -e $ll ]]; then
        echo "output file written despite the error"
    elif [[ $(cat "$TMP/err") != "$(cat "${src%.txt}.expected")" ]]; then
        echo "got $(tail -1 "$TMP/err")"
    fi
}

for flag in "" --optimize; do
    for src in tests/ok/*.txt tests/opt/*.txt; do
        base=${src%.txt}
        input=/dev/null
        [[ -e $base.in ]] && input=$base.in
        report "${base#tests/} $flag" "$(run_ok "$src" "$input" "$base.expected" "$flag")"
    done
    for src in tests/err/*.txt; do
        base=${src%.txt}
        report "${base#tests/} $flag" "$(run_err "$src" "$flag")"
    done
    for session in programs/*.in; do
        base=${session%.in}
        prog=programs/$(basename "${base%%.*}").txt
        report "${base} $flag" "$(run_ok "$prog" "$session" "$base.expected" "$flag")"
    done
done

for AST in tests/ok/*.ast; do
    report "$AST" "$(compare_ast --ast "${AST%.ast}.txt")"
done
for AST in tests/opt/*.ast; do
    report "$AST" "$(compare_ast --optimize --ast "${AST%.ast}.txt")"
done

echo
echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
