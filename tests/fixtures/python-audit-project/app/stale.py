def stale_function() -> str:
    return "stale"


class StaleClass:
    def stale_method(self) -> str:
        return "stale"

    @property
    def stale_property(self) -> str:
        return "stale"


def unreachable() -> str:
    return "done"
    return "unreachable"
