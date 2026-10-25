"""A tiny ICS reader for assertions (unfolds lines, groups events). Not a general parser."""


def unfold(text: str) -> list[str]:
    """Undo RFC 5545 line folding: a CRLF followed by one space/tab continues the line."""
    return text.replace("\r\n ", "").replace("\r\n\t", "").split("\r\n")


def parse_events(text: str) -> list[dict[str, str]]:
    """Events as {property-with-params: raw value}, in file order."""
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            events.append(current or {})
            current = None
        elif current is not None and line:
            name, _, value = line.partition(":")
            current[name] = value
    return events
