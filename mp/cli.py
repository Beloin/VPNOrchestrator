#!/usr/bin/env python3
"""mp CLI — management plane for the SDN service chain.

Commands:
  use FILE    write FILE as current.yaml into the specs dir
  validate    validate the resolved spec (current.yaml, or default.yaml)
  show        print the resolved chain
  run         generate click configs + docker-compose from the resolved spec

Spec lives in MP_SPEC_DIR (default ./specs, mount as a Docker volume).
"""

import argparse
import shutil
import sys
from pathlib import Path

import codegen
import spec


def cmd_use(args: argparse.Namespace) -> int:
    src = Path(args.file)
    _, errs = spec.load_file(src)
    if errs:
        for e in errs:
            print(f"error: {e}", file=sys.stderr)
        return 1
    dest = spec.SPEC_DIR / "current.yaml"
    shutil.copy(src, dest)
    print(f"current := {src} -> {dest}")
    return 0


def _resolved() -> tuple[spec.Spec, spec.SpecSource]:
    """resolve() + SpecError -> exit code 1 handling."""
    try:
        return spec.resolve()
    except spec.SpecError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e


def cmd_show(args: argparse.Namespace) -> int:
    del args
    chain, source = _resolved()
    print(f"# {source.value}")
    print(str(chain))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    del args
    _, source = _resolved()
    print(f"{source.value}: valid")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    chain, source = _resolved()
    out = Path(args.out)
    files = codegen.gen_config(chain)
    files = {f"{n}.click": c for n, c in files.items()} | {"docker-compose.yml": codegen.gen_compose(chain)}
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
    print(f"# {source.value} -> {out}/")
    for name in files:
        print(name)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="mp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("use", help="set a spec file as current.yaml")
    u.add_argument("file")
    u.set_defaults(fn=cmd_use)
    s = sub.add_parser("show", help="print the resolved chain")
    s.set_defaults(fn=cmd_show)
    v = sub.add_parser("validate", help="validate the resolved spec")
    v.set_defaults(fn=cmd_validate)
    r = sub.add_parser("run", help="generate click configs + compose from the resolved spec")
    r.add_argument("--out", default="gen", help="generated files directory (default gen/)")
    r.set_defaults(fn=cmd_run)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())