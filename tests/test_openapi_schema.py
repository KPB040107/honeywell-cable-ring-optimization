"""Regression coverage for the public API schema."""

import warnings

from resplan_router.phase7_interactive.server import app


def test_openapi_schema_builds_with_unique_operation_ids():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()

    operations = [
        operation
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert operations
    assert len({operation["operationId"] for operation in operations}) == len(operations)
    assert {"get", "post"}.issubset(schema["paths"]["/api/demo/{demo_id}"])
    assert not [warning for warning in caught if "Duplicate Operation ID" in str(warning.message)]
