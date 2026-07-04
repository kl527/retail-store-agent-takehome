"""Domain-level failures that the agent should relay, not crash on."""


class DomainError(Exception):
    """A business-rule violation or invalid request (e.g. insufficient stock).

    `details` is JSON-serializable context the LLM can use to explain the
    problem to the user (e.g. {"sku": "TOTE", "requested": 10, "on_hand": 4}).
    """

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details
