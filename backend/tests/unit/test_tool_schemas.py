import pytest
from pydantic import ValidationError

from app.tools.schemas import CreateReturnInput, OrderLookupInput, PolicyInput


def test_rejects_extra_arguments_such_as_a_smuggled_customer_id() -> None:
    with pytest.raises(ValidationError, match="customer_id"):
        OrderLookupInput.model_validate({"order_id": 4821, "customer_id": 1002})


@pytest.mark.parametrize("order_id", [0, -5])
def test_rejects_non_positive_order_ids(order_id: int) -> None:
    with pytest.raises(ValidationError):
        OrderLookupInput(order_id=order_id)


def test_rejects_empty_return_reason() -> None:
    with pytest.raises(ValidationError):
        CreateReturnInput(order_id=5532, reason="")


def test_rejects_unknown_policy_type() -> None:
    with pytest.raises(ValidationError):
        PolicyInput.model_validate({"policy_type": "everything"})


def test_input_schema_is_exportable_for_tool_calling() -> None:
    schema = OrderLookupInput.model_json_schema()

    assert schema["required"] == ["order_id"]
    assert schema["additionalProperties"] is False
