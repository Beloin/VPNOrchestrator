# Management plane targets.

target(
    name = "test",
    driver = "exec",
    # Spec parsing tests: raw yaml -> instantiated Spec/Setup classes.
    deps = {"src": glob("*.py"), "schema": file("schema.json")},
    tools = ["//:mppython|python3"],
    run = ["python3", "test_spec.py"],
)