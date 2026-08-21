"""Execution safety primitives for the paper-only platform."""


class ExecutionForbidden(RuntimeError):
    """Raised whenever legacy code attempts to execute a financial action."""

