class SubtransError(Exception):
    """Fails one file. The sweep logs it and continues with the next file."""


class FatalError(SubtransError):
    """Fails every file in the same way, for example a bad API key. The sweep stops."""
