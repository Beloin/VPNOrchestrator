# Management-plane python environment.
#
# mppython: a single-file "python3" tool. Build side: pip-installs
# mp/requirements.txt into a staging tree, appends it as a tar payload and
# rewrites the payload offset. Consume side: the tool extracts the payload to
# a mktemp dir and execs the system python with PYTHONPATH pointing at it.
# Consumers just do:
#     tools = ["//:mppython|python3"]
#     run   = ["python3", "script.py", ...]
# Launcher shell script: this is a self-extracting tar that should be a python
# venv — a single-file tool carrying the pip packages (payload tar) appended to
# a sh header, because this heph build only allows one-file tool outputs.
# Written with a fixed-width (10 digits) placeholder
# offset that gets patched to the real payload position below. The placeholder
# width must not change the header size; `tail -c +N` is 1-indexed, so the real
# offset = header size + 1 (first byte after the header).
_MP_LAUNCHER = """#!/bin/sh
set -e
T=$(mktemp -d)
tail -c +0000000000 "$0" > $T/payload.tar
tar -xf $T/payload.tar -C $T
PYTHONPATH=$T/lib exec /usr/bin/python3 "$@"
"""

target(
    name = "mppython",
    driver = "bash",
    deps = {"requirements": file("mp/requirements.txt")},
    tools = ["//@heph/bin:bash"],
    out = {"python3": "python3"},
    run = [
        # bash driver joins run strings with spaces; one logical chain.
        # Absolute /usr/bin/python3: //@heph/bin:python3 resolves to the pyenv shim,
        # which loops inside the sandbox.
        "mkdir -p stage/lib &&",
        "/usr/bin/python3 -m pip install --target stage/lib --quiet -r mp/requirements.txt &&",
        "tar -cf payload.tar -C stage lib &&",
        "printf '%s\\n' \"$MP_LAUNCHER\" > python3 &&",
        "OFF=$(($(wc -c < python3)+1)) &&",
        "sed -i \"s/+0000000000/+$(printf '%010d' $OFF)/\" python3 &&",
        "cat payload.tar >> python3 &&",
        "chmod +x python3",
    ],
    env = {"MP_LAUNCHER": _MP_LAUNCHER},
)

# Management-plane CLI. Args via env — heph cannot pass args into its runner:
#   MP_ARGS=show heph run //:mp --cat-out
target(
    name = "mp-show",
    driver = "exec",
    deps = {
        "src": glob("mp/*.py"),
        "schema": file("mp/schema.json"),
        "specs": glob("mp/specs/*.yaml"),
    },
    tools = ["//:mppython|python3"],
    pass_env = ["MP_ARGS"],
    env = {"MP_SPEC_DIR": "mp/specs"},
    out = {"cli": "cli-out.txt"},
    run = [
        "/usr/bin/sh",
        "-c",
        "exec python3 mp/cli.py ${MP_ARGS:-show} > $OUT_CLI",
    ],
)

# Data plane: Click modular-router image (userlevel).
#
# One image, many routers. The orchestrator instantiates one container per
# router and feeds each its own Click config (mounted ConfigMap at
# /etc/click/router.click). Config is runtime data, never baked into the image.

target(
    name = "click_image",
    driver = "exec",
    # The Dockerfile is the only thing docker reads from the build context
    # today, so it is the only declared input. If dp/ gains files that get
    # COPY'd or otherwise affect the build, switch this to glob("dp/**").
    deps = {"dockerfile": file("dp/Dockerfile")},
    tools = "//@heph/bin:docker",
    run = ["docker", "build", "-t", "click", "./dp"],
)

# Future targets (not declared yet — mp/ is empty):
#   //mp:orchestrator  — Python orchestrator (orchestrator.py) that reads a YAML
#                        spec and emits Click configs + K8s manifests.
#   K8s manifest targets — once mp/ produces manifests, add targets here.
