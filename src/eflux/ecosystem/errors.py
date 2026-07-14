"""Domain errors shared by ecosystem services and transport adapters."""


class EcosystemError(Exception):
    """A stable domain error with an adapter-facing status classification."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
