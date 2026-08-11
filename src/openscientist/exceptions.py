"""
Structured exception hierarchy for OpenScientist.
"""


class OpenScientistError(Exception):
    """Base exception for all OpenScientist errors."""


# ── Code execution ────────────────────────────────────────────────────


class CodeExecutionError(OpenScientistError):
    """Raised when user-submitted code execution fails."""


class CodeExecutionTimeoutError(CodeExecutionError):
    """Raised when code execution exceeds its time limit."""


class ForbiddenImportError(CodeExecutionError):
    """Raised when code tries to import a disallowed module."""


# ── File loading / upload ─────────────────────────────────────────────


class FileLoadError(OpenScientistError):
    """Raised when a data file cannot be loaded or parsed."""


class FileTooBigError(FileLoadError):
    """Raised when a file exceeds the size limit."""


class UnsupportedFileTypeError(FileLoadError):
    """Raised when a file type is not supported."""


# ── Budget / cost tracking ────────────────────────────────────────────


class BudgetExceededError(OpenScientistError):
    """Raised when a job exceeds its budget limit."""


# ── Provider errors ───────────────────────────────────────────────────


class ProviderError(OpenScientistError):
    """Raised when a model-provider operation (API call, billing query) fails."""


# ── PDF generation ────────────────────────────────────────────────────


class PDFGenerationError(OpenScientistError):
    """Raised when PDF report generation fails."""


class McpToolsUnavailableError(OpenScientistError):
    """The agent ran without the openscientist-tools MCP tools.

    Fatal rather than a failed turn: the agent cannot analyse or record
    anything, and the condition persists across turns, so continuing produces a
    report with nothing behind it (openscientist-io#263).
    """
