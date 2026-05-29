class DolphinError(Exception):
    """Base exception for dolphin_desktop."""


class ElementNotFoundError(DolphinError):
    """Raised when an element cannot be found within the timeout."""


class WaitTimeoutError(DolphinError):
    """Raised when a wait condition is not met within the allowed time."""


class ApplicationError(DolphinError):
    """Raised when application launch or connection fails."""


class WindowNotFoundError(DolphinError):
    """Raised when a window cannot be found."""


class AliasNotFoundError(DolphinError):
    """Raised when an alias is not found in the Object Repository."""
