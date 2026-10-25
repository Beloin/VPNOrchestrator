# Management plane targets.

target(
    name = "test",
    driver = "exec",
    # Spec parsing tests: raw yaml -> instantiated Spec/Setup classes.
    deps = {"src": glob("*.py"), "schema": file("schema.json")},
    tools = ["//:mppython|python3"],
    run = ["python3", "test_spec.py"],
)

# Generate click configs + compose from the resolved spec and validate them
# against real Click instances (docker): every node's config must parse and
# the pinger must get pings through to the sink (or not, for a full-drop node).
target(
    name = "click-validate",
    driver = "exec",
    deps = {
        "src": glob("*.py"),
        "schema": file("schema.json"),
        "specs": glob("specs/*.yaml"),
        "image": ["//:click_image"],
    },
    tools = ["//:mppython|python3", "//@heph/bin:docker"],
    env = {"MP_SPEC_DIR": "specs"},
    run = ["/usr/bin/sh", "-c", "python3 validate_click.py"],
)