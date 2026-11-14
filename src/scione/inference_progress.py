"""Best-effort, source-free progress for explicitly planned inference calls."""

from threading import RLock

from scione.providers.base import ModelProvider

STAGE_MESSAGES = {
    "planning": "Preparing the analysis plan.",
    "inventory": "Identifying requested tests.",
    "extracting": "Reading test requirements.",
    "matching": "Comparing requirements with lab capabilities.",
    "validating": "Checking the returned results.",
    "saving": "Saving the checked results.",
}


def progress_snapshot(stage, completed=0, total=None):
    return {
        "stage": stage, "completed_steps": completed, "total_steps": total,
        "percent": round(100 * completed / total, 1) if total else None,
        "message": STAGE_MESSAGES[stage],
    }


class ProgressReporter:
    """Serialize worker notifications; telemetry failures never fail inference."""

    def __init__(self, callback=None):
        self.callback = callback
        self._lock = RLock()
        self._completed = 0
        self._total = None

    def _publish(self, stage):
        if self.callback is not None:
            try:
                self.callback(progress_snapshot(stage, self._completed, self._total))
            except Exception:
                pass

    def emit(self, stage):
        with self._lock:
            self._publish(stage)

    def plan(self, total, *, stage):
        with self._lock:
            self._total = total
            self._publish(stage)

    def step(self, *, stage):
        with self._lock:
            self._completed += 1
            self._publish(stage)

    def forward(self, event):
        with self._lock:
            self._completed = event["completed_steps"]
            self._total = event["total_steps"]
            self._publish(event["stage"])


class ProgressProvider(ModelProvider):
    """An observation wrapper; the original request and response pass unchanged."""

    def __init__(self, provider, reporter, *, stage):
        self.provider, self.reporter, self.stage = provider, reporter, stage

    @property
    def provider_name(self):
        return self.provider.provider_name

    @property
    def model_name(self):
        return self.provider.model_name

    @property
    def generation_settings(self):
        return self.provider.generation_settings

    def generate_structured(self, request):
        try:
            return self.provider.generate_structured(request)
        finally:
            self.reporter.step(stage=self.stage)
