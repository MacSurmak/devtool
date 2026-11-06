"""Domain exception hierarchy for devtool."""


class DevtoolError(Exception):
    """Base exception for all devtool domain errors."""


class CommandExecutionError(DevtoolError):
    """Raised when an external subprocess or git command fails."""


class ConfigurationError(DevtoolError):
    """Raised when devtool configuration or workspace state is invalid."""


class PatchRejectedError(DevtoolError):
    """Raised when a patch violates structural or architectural validation rules."""
