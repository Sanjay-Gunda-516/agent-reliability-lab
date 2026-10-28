import pytest
from sqlalchemy.exc import OperationalError

from app.agent.config import AgentConfig
from app.agent.llm import LLMError
from app.scenarios.faults import InjectedFault, apply_faults
from app.scenarios.schema import FaultSpec, FaultType
from tests.fakes import FakeLLM, text


def fault(type_: str, tool: str | None = None, times: int | None = None) -> FaultSpec:
    return FaultSpec(type=FaultType(type_), tool=tool, times=times)


@pytest.mark.parametrize(
    ("fault_type", "exception"),
    [
        ("tool_timeout", TimeoutError),
        ("database_error", OperationalError),
        ("tool_crash", RuntimeError),
    ],
)
def test_tool_faults_raise_what_real_failures_raise(
    fault_type: str, exception: type[Exception]
) -> None:
    _, injector, log = apply_faults([fault(fault_type, "get_order")], FakeLLM([]))
    assert injector is not None

    with pytest.raises(exception):
        injector("get_order")
    assert log == [InjectedFault("get_order", FaultType(fault_type))]


def test_limited_fault_recovers_after_n_failures() -> None:
    _, injector, log = apply_faults([fault("tool_timeout", "cancel_order", times=2)], FakeLLM([]))
    assert injector is not None

    for _ in range(2):
        with pytest.raises(TimeoutError):
            injector("cancel_order")
    injector("cancel_order")  # third attempt goes through

    assert len(log) == 2


def test_faults_only_hit_their_own_tool() -> None:
    _, injector, log = apply_faults([fault("database_error", "get_order")], FakeLLM([]))
    assert injector is not None

    injector("list_orders")

    assert log == []


def test_unlimited_fault_never_recovers() -> None:
    _, injector, _ = apply_faults([fault("database_error", "get_order")], FakeLLM([]))
    assert injector is not None

    for _ in range(5):
        with pytest.raises(OperationalError):
            injector("get_order")


def test_llm_fault_fails_first_calls_then_passes_through() -> None:
    inner = FakeLLM([text("hello")])
    llm, injector, log = apply_faults([fault("llm_unavailable", times=1)], inner)
    config = AgentConfig()

    with pytest.raises(LLMError):
        llm.classify_intent("Customer: hi", config)
    reply = llm.respond(system="", messages=[], tools=[], config=config)

    assert injector is None
    assert reply.content == [{"type": "text", "text": "hello"}]
    assert log == [InjectedFault("llm", FaultType.LLM_UNAVAILABLE)]


def test_no_faults_means_no_wrapping() -> None:
    inner = FakeLLM([])

    llm, injector, log = apply_faults([], inner)

    assert llm is inner
    assert injector is None
    assert log == []
