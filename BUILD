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
