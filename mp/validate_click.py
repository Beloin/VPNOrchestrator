#!/usr/bin/env python3
"""Validate generated Click setups against real Click instances in docker.

Flow: resolve spec -> codegen (configs + compose) -> write to a temp dir ->
docker compose up -> ping from pinger to sink through the chain -> down.

A drop node with percent 100 is expected to blackhole traffic; in that case
the assertion is flipped (the pings must FAIL to get through).
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import codegen
import functions
import spec


def sh(args: list[str], **kw) -> subprocess.CompletedProcess:
    p = subprocess.run(args, capture_output=True, text=True, **kw)
    return p


def validate() -> int:
    chain, source = spec.resolve()
    print(f"# {source.value}")

    gen_dir = Path(tempfile.mkdtemp(prefix="mp_validate_"))
    files = {f"{n}.click": c for n, c in codegen.gen_config(chain).items()}
    files["docker-compose.yml"] = codegen.gen_compose(chain)
    for name, text in files.items():
        (gen_dir / name).write_text(text)

    pinger_ip, sink_ip = codegen.plan_topology(chain).pinger.ip, codegen.plan_topology(chain).sink.ip
    # a full-drop node must blackhole; anything else must get pings through
    expect_loss = any(
        n.function == functions.FunctionName.DROP and (n.setup.percent or 0) >= 100
        for n in chain.nodes
    )
    project = "mp_validate"

    try:
        up = sh(["docker", "compose", "-p", project, "-f", str(gen_dir / "docker-compose.yml"), "up", "-d"])
        if up.returncode:
            print(f"compose up failed:\n{up.stderr}", file=sys.stderr)
            return 1
        # nodes' click instances keep running iff their configs parse+init
        time.sleep(5)
        for node in chain.nodes:
            st = sh(["docker", "inspect", "-f", "{{.State.Status}}", f"{project}-{node.name}-1"])
            if st.stdout.strip() != "running":
                logs = sh(["docker", "logs", f"{project}-{node.name}-1"])
                print(f"{node.name}: not running ({st.stdout or st.stderr.strip()}):\n{logs.stdout}", file=sys.stderr)
                return 1
        print(f"# all {len(chain.nodes)} click instances running")

        time.sleep(2)
        ping = sh(
            ["docker", "compose", "-p", project, "exec", "-T", "pinger",
             "ping", "-c", "5", "-W", "2", sink_ip],
        )
        replies = sum(1 for line in ping.stdout.splitlines() if "time=" in line)
        ok = (not expect_loss and replies > 0) or (expect_loss and replies == 0)
        print(f"# ping {pinger_ip} -> {sink_ip}: {replies}/5 replies, expect_loss={expect_loss}")
        if not ok:
            print(f"ping failed:\n{ping.stdout}\n{ping.stderr}", file=sys.stderr)
            return 1
        print("PASS click-validate")
        return 0
    finally:
        sh(["docker", "compose", "-p", project, "-f", str(gen_dir / "docker-compose.yml"), "down", "-v"])


if __name__ == "__main__":
    sys.exit(validate())