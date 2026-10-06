import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def run(src):
    out = ROOT / "output.check.ll"
    out.unlink(missing_ok=True)
    expected = src.with_suffix(".expected").read_text().rstrip("\n")
    compiled = subprocess.run(
        [sys.executable, "compiler.py", str(src), str(out)],
        cwd=ROOT, capture_output=True, text=True,
    )

    if src.parent.name == "err":
        if compiled.returncode == 0:
            return False, "compiled, expected an error"
        if out.exists():
            out.unlink()
            return False, "wrote an output file"
        actual = compiled.stderr.rstrip("\n")
    else:
        if compiled.returncode != 0:
            return False, compiled.stderr.strip().splitlines()[-1]
        ran = subprocess.run(["lli", str(out)], cwd=ROOT, capture_output=True, text=True)
        out.unlink(missing_ok=True)
        if ran.returncode != 0:
            return False, "lli failed"
        actual = ran.stdout.rstrip("\n")

    if actual != expected:
        return False, f"got {actual!r}"
    return True, actual


def main():
    tests = sorted(ROOT.glob("tests/*/*.txt"))
    if not tests:
        print("no tests found", file=sys.stderr)
        return 2

    width = max(len(str(t.relative_to(ROOT / "tests"))) for t in tests)
    failed = 0
    for src in tests:
        ok, detail = run(src)
        failed += not ok
        name = str(src.relative_to(ROOT / "tests"))
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")

    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
