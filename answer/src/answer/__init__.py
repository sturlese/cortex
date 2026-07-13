"""answer — the serving half with the pipeline's guarantees.

The page contract told clients how to behave ("don't quote numbers from failed pages", "open the
original when detail_in_source"); this package ENFORCES it server-side: hybrid retrieval that
demotes stale/untrusted pages, exact numeric answers from the facts store, and an answering agent
whose every figure is judged by a deterministic verifier before the answer leaves the server.
"""
__all__ = ["service", "index", "retrieve", "metrics", "synthesize", "verify_answer", "settings"]
