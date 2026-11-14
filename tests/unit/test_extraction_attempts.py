"""Extraction failure recording stops at the failed call and never accepts partials."""

import pytest
from test_customer_requirements import extraction_payload, source_document

from scione.providers.base import ProviderError, TokenUsage
from scione.providers.static import StaticModelProvider
from scione.specifications.extraction import CustomerExtractionError, CustomerRequirementRunner


class SecondPassFailure(StaticModelProvider):
    def __init__(self):
        super().__init__(extraction_payload())
        self.calls = 0

    @property
    def generation_settings(self):
        return {"temperature": 0.0, "max_completion_tokens": 4096}

    def generate_structured(self, request):
        self.calls += 1
        if self.calls == 2:
            raise ProviderError(
                "A complete JSON answer was not returned.",
                raw_text='{"unfinished":',
                usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
                provider="static",
                model="actual-failed-model",
                generation_settings=self.generation_settings,
            )
        if self.calls > 2:
            pytest.fail("A failed pass must not trigger another request.")
        return super().generate_structured(request)


def test_second_pass_failure_retains_first_answer_and_original_failed_request():
    document = source_document()
    # Seven pages plan three calls; the second failure must prevent the third.
    document.pages = [
        document.pages[index % 2].model_copy(update={"page_number": index + 1}, deep=True)
        for index in range(7)
    ]
    document.page_count = 7
    provider = SecondPassFailure()
    with pytest.raises(CustomerExtractionError) as captured:
        CustomerRequirementRunner(provider).run(document, strategy="focused_passes")
    assert provider.calls == 2
    artifact = captured.value.artifact
    assert artifact.status == "failed" and artifact.error_code == "provider_error"
    assert artifact.generation_settings["temperature"] == 0.0
    assert len(artifact.calls) == 2
    first, second = artifact.calls
    assert first.focus_pages == [1, 2, 3]
    assert first.response.parsed == extraction_payload()
    assert first.failure is None
    assert second.focus_pages == [4, 5, 6]
    assert "physical pages 4, 5, 6" in second.request.user_prompt
    assert second.response is None
    assert second.failure.raw_text == '{"unfinished":'
    assert second.failure.usage.total_tokens == 30
    assert second.failure.generation_settings["max_completion_tokens"] == 4096
    assert "unfinished" not in str(captured.value)
