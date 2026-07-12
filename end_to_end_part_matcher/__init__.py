"""Single-source-part end-to-end retrieval, MPN, n-gram, and LLM matching pipeline."""

__all__ = ["PipelineConfig", "match_source_part"]


def __getattr__(name):
    if name in __all__:
        from .pipeline import PipelineConfig, match_source_part

        return {"PipelineConfig": PipelineConfig, "match_source_part": match_source_part}[name]
    raise AttributeError(name)
