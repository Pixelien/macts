"""Paylaşılan LLM istemci katmanı (Faz 3, Aşama 2).

ai_analyst dışındaki agent'lar da ileride LLM çağırmak isterse bu katmanı
kullanır. Tasarım kararları: docs/AI_ANALYST_MODEL_SELECTION.md.
"""

from src.core.llm.config import LLMConfig, LLMModelConfig, load_llm_config
from src.core.llm.fallback_chain import AllModelsFailedError, FallbackChain
from src.core.llm.nvidia_client import (
    NIMError,
    NIMNotFoundError,
    NIMQuotaError,
    NIMRateLimitError,
    NIMServerError,
    NvidiaNIMClient,
)
from src.core.llm.prompts import PromptTemplate, build_messages, load_prompt
from src.core.llm.rate_limiter import TokenBucketLimiter
from src.core.llm.tracking import AnalysisCache, UsageTracker, build_cache_key
from src.core.llm.utils import compute_backoff, extract_json

__all__ = [
    "AllModelsFailedError", "AnalysisCache", "FallbackChain", "LLMConfig",
    "LLMModelConfig", "NIMError", "NIMNotFoundError", "NIMQuotaError",
    "NIMRateLimitError", "NIMServerError", "NvidiaNIMClient", "PromptTemplate",
    "TokenBucketLimiter", "UsageTracker", "build_cache_key", "build_messages",
    "compute_backoff", "extract_json", "load_llm_config", "load_prompt",
]
