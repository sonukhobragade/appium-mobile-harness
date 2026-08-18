"""
API Contract Validator — Pydantic-based type and structure validation.

Called by api_fetcher._fetch_endpoint() immediately after schema.json validation.
Results are logged to change_log.json with type="contract_violation".

The conftest.py backend_data fixture reads change_log.json and:
  - Always logs violations as warnings
  - pytest.fail() if STRICT_API_CONTRACTS=true (recommended for CI)

Usage:
    from src.utils.api_contract_validator import validate_contract

    result = validate_contract("subscription_plans", data)
    if not result.valid:
        log_change("subscription_plans", "contract_violation", {
            "error_count": result.error_count,
            "errors": result.errors,
            "summary": result.summary,
        })
"""

import logging
from dataclasses import dataclass, field

from pydantic import TypeAdapter, ValidationError

from src.data.models.api_contracts import CONTRACTS

logger = logging.getLogger(__name__)

# Cache TypeAdapters at module level — schema compilation happens once per process
_ADAPTER_CACHE: dict[str, TypeAdapter] = {
    name: TypeAdapter(contract_type)
    for name, contract_type in CONTRACTS.items()
}


@dataclass
class ContractResult:
    valid: bool
    summary: str = "ok"
    error_count: int = 0
    errors: list[dict] = field(default_factory=list)


def validate_contract(endpoint_name: str, data) -> ContractResult:
    """
    Validate API response data against the Pydantic contract for this endpoint.

    Args:
        endpoint_name: Key from api_endpoints.json (e.g. "subscription_plans")
        data: The extracted API response data (after response_key unwrapping)

    Returns:
        ContractResult with valid=True if no violations, otherwise details of
        which fields failed and why.

    Contract not defined → returns valid=True with summary="no_contract".
    This is intentional: endpoints without a contract are not blocked.
    Add a contract to CONTRACTS in api_contracts.py to start validating.
    """
    adapter = _ADAPTER_CACHE.get(endpoint_name)

    if adapter is None:
        logger.debug(f"[{endpoint_name}] No contract defined — skipping type validation")
        return ContractResult(valid=True, summary="no_contract")

    if data is None:
        logger.debug(f"[{endpoint_name}] data is None — skipping contract validation")
        return ContractResult(valid=True, summary="no_data")

    try:
        adapter.validate_python(data)
        logger.debug(f"[{endpoint_name}] Contract validation passed")
        return ContractResult(valid=True, summary="ok")

    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            # Build a readable field path: e.g. "[0] → extraInfo → sideButton"
            loc_parts = [str(p) for p in err["loc"]]
            field_path = " → ".join(loc_parts) if loc_parts else "(root)"

            errors.append({
                "field": field_path,
                "type": err["type"],       # e.g. "missing", "string_type", "float_type"
                "message": err["msg"],     # e.g. "Field required", "Input should be a valid float"
            })

        summary_parts = []
        for e in errors[:3]:  # first 3 in summary line
            summary_parts.append(f"{e['field']} ({e['type']})")
        summary = "; ".join(summary_parts)
        if len(errors) > 3:
            summary += f" ... +{len(errors) - 3} more"

        logger.warning(
            f"[{endpoint_name}] CONTRACT VIOLATION — {len(errors)} error(s): {summary}"
        )
        for err in errors:
            logger.warning(f"  [{endpoint_name}]  {err['field']}: {err['message']}")

        return ContractResult(
            valid=False,
            summary=summary,
            error_count=len(errors),
            errors=errors,
        )
