import asyncio
import re
from collections.abc import Iterator
from pathlib import Path

import yaml
from fastapi import Body
from httpx import ASGITransport, AsyncClient, Response

from lucking.api.main import create_app

CONTRACT_PATH = Path("specs/009-investment-workbench/contracts/openapi.yaml")
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
ENVELOPE_FIELDS = {"code", "message", "data", "errors", "request_id", "timestamp"}


def load_contract() -> dict[str, object]:
    return yaml.safe_load(CONTRACT_PATH.read_text())


def operations(contract: dict[str, object]) -> Iterator[tuple[str, str, dict[str, object]]]:
    paths = contract["paths"]
    assert isinstance(paths, dict)
    for path, path_item in paths.items():
        assert isinstance(path, str)
        assert isinstance(path_item, dict)
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                assert isinstance(operation, dict)
                yield path, method, operation


def operation_index(
    contract: dict[str, object], *, prefix: str = ""
) -> dict[tuple[str, str], tuple[str, set[str]]]:
    return {
        (path.removeprefix(prefix), method): (
            str(operation["operationId"]),
            set(operation["responses"]),
        )
        for path, method, operation in operations(contract)
    }


def test_design_contract_has_unique_operations_and_required_errors() -> None:
    contract = load_contract()
    all_operations = list(operations(contract))
    operation_ids = [operation["operationId"] for _, _, operation in all_operations]

    assert len(all_operations) == 23
    assert len(operation_ids) == len(set(operation_ids))
    assert all("422" not in operation["responses"] for _, _, operation in all_operations)
    assert all("500" in operation["responses"] for _, _, operation in all_operations)
    assert all(
        "401" in operation["responses"]
        for path, _, operation in all_operations
        if path != "/auth/login"
    )
    assert all(
        "403" in operation["responses"]
        for path, method, operation in all_operations
        if method in {"post", "put", "patch", "delete"} and path != "/auth/login"
    )


def test_design_contract_registers_stable_business_codes_and_typed_envelopes() -> None:
    contract = load_contract()
    components = contract["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)

    assert schemas["BusinessErrorCode"]["enum"] == [
        100001,
        100002,
        100003,
        200001,
        200002,
        200003,
        300001,
        400001,
        400002,
        400003,
        400004,
        500001,
        500002,
        900001,
    ]
    assert set(schemas["ErrorResponse"]["required"]) == ENVELOPE_FIELDS
    assert schemas["ErrorResponse"]["properties"]["data"] == {"type": "null"}
    assert schemas["Pagination"]["required"] == ["limit", "offset", "total", "has_more"]

    response_schemas = [name for name in schemas if name.endswith("Response")]
    assert response_schemas
    for name in response_schemas:
        schema = schemas[name]
        assert schema.get("type") != "object" or schema.get("properties") or schema.get("allOf")


def test_pagination_is_nested_under_data() -> None:
    schemas = load_contract()["components"]["schemas"]
    page_schemas = [name for name in schemas if name.endswith("PageData")]
    assert page_schemas
    for name in page_schemas:
        assert set(schemas[name]["required"]) == {"items", "pagination"}


def test_runtime_validation_uses_400_envelope_without_default_422() -> None:
    app = create_app()

    @app.post("/_contract-validation")
    def validation_probe(value: int = Body(embed=True, ge=1)) -> dict[str, int]:
        return {"value": value}

    async def request_validation_probe() -> Response:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/_contract-validation", json={"value": 0})

    response = asyncio.run(request_validation_probe())

    assert response.status_code == 400
    assert set(response.json()) == ENVELOPE_FIELDS
    assert response.json()["code"] == 200001
    assert response.json()["data"] is None
    assert response.json()["request_id"]
    assert response.json()["timestamp"].endswith("Z")
    runtime_contract = app.openapi()
    assert "422" not in runtime_contract["paths"]["/_contract-validation"]["post"]["responses"]


def test_runtime_paths_operation_ids_statuses_and_codes_match_design_contract() -> None:
    design = load_contract()
    runtime = create_app().openapi()

    runtime_operations = operation_index(runtime, prefix="/api/v1")
    design_operations = operation_index(design)
    assert runtime_operations == design_operations
    assert runtime["components"]["schemas"]["BusinessErrorCode"]["enum"] == design[
        "components"
    ]["schemas"]["BusinessErrorCode"]["enum"]


def test_every_runtime_public_response_is_typed_and_generated_types_have_no_any() -> None:
    runtime = create_app().openapi()
    for _, _, operation in operations(runtime):
        for status, response in operation["responses"].items():
            if status == "204":
                continue
            schema = response["content"]["application/json"]["schema"]
            assert schema
            assert schema != {}
        assert "422" not in operation["responses"]

    generated = Path("frontend/src/api/generated/schema.d.ts").read_text(encoding="utf-8")
    assert re.search(r"\bany\b", generated) is None
    assert "[key: string]: any" not in generated


def test_runtime_domain_enums_match_the_design_contract() -> None:
    design = load_contract()["components"]["schemas"]
    runtime = create_app().openapi()["components"]["schemas"]

    def enum_values(schema: dict[str, object]) -> list[str]:
        if "$ref" in schema:
            return enum_values(runtime[str(schema["$ref"]).rsplit("/", 1)[-1]])
        if "const" in schema:
            return [str(schema["const"])]
        return [str(value) for value in schema["enum"]]

    assert enum_values(runtime["CalendarDayDto"]["properties"]["market_code"]) == ["CN-S"]
    assert (
        enum_values(runtime["CalendarDayDto"]["properties"]["market_status"])
        == design["CalendarDay"]["properties"]["market_status"]["enum"]
    )
    assert (
        enum_values(runtime["StockDetailDto"]["properties"]["market_data_status"])
        == design["StockDetail"]["allOf"][1]["properties"]["market_data_status"]["enum"]
    )
    assert (
        enum_values(runtime["TaskStatusItemDto"]["properties"]["status"])
        == design["TaskStatusItem"]["properties"]["status"]["enum"]
    )
    assert enum_values(runtime["TaskSummaryDto"]["properties"]["status"]) == [
        "BUILDING",
        "READY",
        "FAILED",
    ]
    assert enum_values(runtime["TaskSummaryDto"]["properties"]["notification_status"]) == [
        "PENDING",
        "SENDING",
        "SENT",
        "FAILED",
    ]
    assert runtime["TaskSummaryDto"]["properties"]["counts"]["$ref"].endswith(
        "/TaskStatusCountsDto"
    )
