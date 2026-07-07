"""Optional agent-run tracing (Pydantic Logfire / OpenTelemetry). Off by default.

When CLEAN_TRACE=logfire and the `logfire` package is installed, every agent run — prompts, tool
calls, budgets, retries — is exported as OpenTelemetry spans. That makes each autonomous decision
inspectable after the fact, which is the observability half of "bounded agency": you can cap what
an agent may do AND see what it did.

Deliberately not a hard dependency: `pip install logfire` (and `logfire auth` or an OTLP endpoint)
only where you want traces. Absent or unconfigured, this is a no-op.
"""
import os


def maybe_instrument(component: str) -> bool:
    """Enable tracing if requested and available. Returns True when instrumentation is active."""
    if os.environ.get("CLEAN_TRACE", "").lower() != "logfire":
        return False
    try:
        import logfire
    except ImportError:
        print(f"[{component}] CLEAN_TRACE=logfire set but the 'logfire' package is not installed "
              "-> tracing disabled (pip install logfire)", flush=True)
        return False
    logfire.configure(service_name=f"cortex-{component}", send_to_logfire="if-token-present")
    logfire.instrument_pydantic_ai()
    return True
