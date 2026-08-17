"""Domain-specific failures with actionable messages."""


class GraphValidationError(ValueError):
    """Raised when a reference graph violates a required invariant."""


class SchemaVersionError(GraphValidationError):
    """Raised when serialized graph data uses an unsupported schema."""
