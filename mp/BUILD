# Management plane targets.

target(
    name = "test",
    driver = "exec",
    # Spec parsing + codegen tests: raw yaml -> Spec/Setup -> click configs + k8s manifests.
    deps = {"src": glob("*.py"), "schema": file("schema.json")},
    tools = ["//:mppython|python3"],
    run = ["python3", "test_spec.py"],
)
