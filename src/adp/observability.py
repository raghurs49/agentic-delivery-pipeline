"""Langfuse tracing behind a tiny interface.

One Langfuse trace per workflow run. The trace id is derived from the Temporal workflow id, so every activity
(including retries) attaches its spans and generations to the same trace. When Langfuse is not configured the
NullTracer is used and the pipeline behaves identically.
"""
import contextlib
import hashlib
import os
from typing import Any, Dict, Iterator, List, Optional


def trace_id_for(workflow_id: str) -> str:
    """Deterministic 32-hex trace id (same scheme as Langfuse's seeded ids)."""
    return hashlib.sha256(workflow_id.encode("utf-8")).hexdigest()[:32]


class _Handle:
    def update(self, **kwargs: Any) -> None:
        return None

    def set_trace_io(self, **kwargs: Any) -> None:  # kept for interface compatibility
        return None


class Tracer:
    enabled = False

    def span(self, name: str, trace_id: str, **kw: Any):  # pragma: no cover - interface
        raise NotImplementedError

    def generation(self, name: str, trace_id: str, **kw: Any):  # pragma: no cover - interface
        raise NotImplementedError

    def score(self, trace_id: str, name: str, value: float, comment: str = "", boolean: bool = False) -> None:
        return None

    def flush(self) -> None:
        return None

    def trace_url(self, trace_id: str) -> str:
        return ""


class NullTracer(Tracer):
    @contextlib.contextmanager
    def span(self, name: str, trace_id: str, **kw: Any) -> Iterator[_Handle]:
        yield _Handle()

    @contextlib.contextmanager
    def generation(self, name: str, trace_id: str, **kw: Any) -> Iterator[_Handle]:
        yield _Handle()


class LangfuseTracer(Tracer):
    """Langfuse Python SDK v4 (observations-first model).

    Trace-level attributes (trace name, session id, tags) are propagated to every observation created inside
    `propagate_attributes`, so every activity passes them; the session id is the Temporal workflow id.
    """
    enabled = True

    def __init__(self, host: str, public_key: str, secret_key: str):
        from langfuse import Langfuse  # imported lazily so the package works without it configured
        self.host = host.rstrip("/")
        self._lf = Langfuse(public_key=public_key, secret_key=secret_key, host=self.host)

    @contextlib.contextmanager
    def span(self, name: str, trace_id: str, *, input: Any = None, metadata: Optional[Dict[str, Any]] = None,
             trace_name: Optional[str] = None, session_id: Optional[str] = None,
             tags: Optional[List[str]] = None) -> Iterator[Any]:
        from langfuse import propagate_attributes
        attrs = {k: v for k, v in (("trace_name", trace_name), ("session_id", session_id), ("tags", tags)) if v}
        with contextlib.ExitStack() as stack:
            if attrs:
                stack.enter_context(propagate_attributes(**attrs))
            sp = stack.enter_context(self._lf.start_as_current_observation(
                name=name, as_type="span", trace_context={"trace_id": trace_id}, input=input, metadata=metadata))
            yield sp

    @contextlib.contextmanager
    def generation(self, name: str, trace_id: str, *, model: str, input: Any,
                   parameters: Optional[Dict[str, Any]] = None) -> Iterator[Any]:
        with self._lf.start_as_current_observation(name=name, as_type="generation", model=model, input=input,
                                                   model_parameters=parameters) as gen:
            yield gen

    def score(self, trace_id: str, name: str, value: float, comment: str = "", boolean: bool = False) -> None:
        self._lf.create_score(trace_id=trace_id, name=name, value=value, comment=comment or None,
                              data_type="BOOLEAN" if boolean else "NUMERIC")

    def flush(self) -> None:
        self._lf.flush()

    def trace_url(self, trace_id: str) -> str:
        return f"{self.host}/trace/{trace_id}"


def build_tracer(host: str = "") -> Tracer:
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not (pk and sk):
        return NullTracer()
    return LangfuseTracer(host or os.environ.get("LANGFUSE_HOST", "http://localhost:3000"), pk, sk)
