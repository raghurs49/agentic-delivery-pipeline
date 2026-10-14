"""Runtime settings, read from environment variables with safe local defaults."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "adp-delivery"
    ollama_url: str = "http://localhost:11434"
    spec_model: str = "qwen2.5:3b-instruct"
    code_model: str = "qwen2.5-coder:3b"
    review_model: str = "qwen2.5:3b-instruct"
    provider: str = "ollama"          # ollama | replay | fake
    recordings_path: str = ""          # used by the replay provider
    record_to: str = ""                # when set with provider=ollama, every generation is recorded here
    output_dir: str = "runs"
    sandbox_timeout_s: int = 20
    llm_timeout_s: int = 180
    langfuse_host: str = "http://localhost:3000"

    @staticmethod
    def from_env() -> "Settings":
        e = os.environ.get
        d = Settings()
        return Settings(
            temporal_address=e("TEMPORAL_ADDRESS", d.temporal_address),
            temporal_namespace=e("TEMPORAL_NAMESPACE", d.temporal_namespace),
            task_queue=e("ADP_TASK_QUEUE", d.task_queue),
            ollama_url=e("OLLAMA_URL", d.ollama_url),
            spec_model=e("ADP_SPEC_MODEL", d.spec_model),
            code_model=e("ADP_CODE_MODEL", d.code_model),
            review_model=e("ADP_REVIEW_MODEL", d.review_model),
            provider=e("ADP_PROVIDER", d.provider),
            recordings_path=e("ADP_RECORDINGS", d.recordings_path),
            record_to=e("ADP_RECORD_TO", d.record_to),
            output_dir=e("ADP_OUTPUT_DIR", d.output_dir),
            sandbox_timeout_s=int(e("ADP_SANDBOX_TIMEOUT_S", d.sandbox_timeout_s)),
            llm_timeout_s=int(e("ADP_LLM_TIMEOUT_S", d.llm_timeout_s)),
            langfuse_host=e("LANGFUSE_HOST", d.langfuse_host),
        )

    def output_path(self) -> Path:
        return Path(self.output_dir)
