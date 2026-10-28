import pytest

from app.domain.policies import PolicyType, get_policy_text


@pytest.mark.parametrize("policy", list(PolicyType))
def test_every_policy_has_text(policy: PolicyType) -> None:
    assert get_policy_text(policy).strip()


def test_policy_text_reflects_rule_values() -> None:
    assert "24 hours" in get_policy_text(PolicyType.CANCELLATION)
    assert "30 days" in get_policy_text(PolicyType.RETURNS)
    assert "$500.00" in get_policy_text(PolicyType.REFUNDS)
