"""Retry/backoff at the LLM boundary, driven by a flaky scripted model. Offline; sleeping is injected so tests are instant."""
import pytest

from tests.test_pipeline import EXTRACTION, GOOD
from veritas.llm import LLMError, RetryingLLM, ScriptedLLM, TransientLLMError
from veritas.pipeline import run


class FlakyLLM:
    """Raises `errors` in order, then behaves like a ScriptedLLM."""
    name = "flaky"

    def __init__(self, errors, responses):
        self.errors, self.inner = list(errors), ScriptedLLM(responses)
        self.attempts = 0
        self.usage = {"calls": 0}

    def complete(self, system, user):
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        self.usage["calls"] += 1
        return self.inner.complete(system, user)


def test_transient_failures_are_retried_with_exponential_backoff():
    sleeps = []
    flaky = FlakyLLM([TransientLLMError("timeout"), TransientLLMError("empty")], ["ok"])
    assert RetryingLLM(flaky, retries=2, base_delay=1.5, sleep=sleeps.append).complete("s", "u") == "ok"
    assert flaky.attempts == 3 and sleeps == [1.5, 3.0]


def test_gives_up_after_the_retry_budget_and_reports_attempts():
    flaky = FlakyLLM([TransientLLMError("boom")] * 5, [])
    sleeps = []
    with pytest.raises(LLMError, match="giving up after 3 attempts: boom"):
        RetryingLLM(flaky, retries=2, sleep=sleeps.append).complete("s", "u")
    assert flaky.attempts == 3 and len(sleeps) == 2


def test_permanent_errors_are_not_retried():
    flaky = FlakyLLM([LLMError("bad request")], ["never"])
    with pytest.raises(LLMError, match="bad request"):
        RetryingLLM(flaky, sleep=lambda s: pytest.fail("must not sleep")).complete("s", "u")
    assert flaky.attempts == 1


def test_usage_is_delegated_to_the_wrapped_model():
    flaky = FlakyLLM([], ["ok"])
    llm = RetryingLLM(flaky)
    llm.complete("s", "u")
    assert llm.usage == {"calls": 1} and llm.name == "retry:flaky"


def test_pipeline_survives_a_flaky_model_and_counts_logical_calls_only(pack):
    flaky = FlakyLLM([TransientLLMError("t")], [EXTRACTION, GOOD])
    res = run("robust", "SRVCABLE", "Sarvottam Cables Ltd", pack, RetryingLLM(flaky, sleep=lambda s: None), use_entailment=False)
    assert res.validation.ok and res.llm_calls == 2 and flaky.attempts == 3


def test_graph_engine_also_survives_a_flaky_model(pack):
    pytest.importorskip("langgraph")
    from veritas.graph import run_graph
    flaky = FlakyLLM([TransientLLMError("t"), TransientLLMError("t")], [EXTRACTION, GOOD])
    res = run_graph("SRVCABLE", "Sarvottam Cables Ltd", pack, RetryingLLM(flaky, sleep=lambda s: None), use_entailment=False)
    assert res.validation.ok and res.llm_calls == 2


def test_exhausted_retries_surface_as_a_clean_cli_error(pack, tmp_path, monkeypatch, capsys):
    import veritas.cli as cli
    monkeypatch.setattr(cli, "make_llm", lambda: RetryingLLM(FlakyLLM([TransientLLMError("down")] * 9, []), retries=1, sleep=lambda s: None))
    assert cli.main(["SRVCABLE", pack, "--out", str(tmp_path / "o.md")]) == 2
    assert "giving up after 2 attempts" in capsys.readouterr().err
