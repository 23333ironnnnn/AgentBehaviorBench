"""ACP terminal evidence seam; independent of Agent and evaluation SDK names."""
from typing import Protocol

MAX_TERMINAL_BYTES = 64 * 1024
MAX_TERMINALS = 256


class TerminalEvidenceSource(Protocol):
    def snapshot(self, terminal_id: str) -> dict | None:
        """Return observed JSON fields, or None when the terminal is unknown."""
        ...


class TerminalEvidenceExtension(TerminalEvidenceSource, Protocol):
    capabilities: dict

    def on_update(self, update: dict) -> None:
        ...

    def identifiers(self) -> tuple[str, ...]:
        ...


def bounded_output(text, limit=MAX_TERMINAL_BYTES):
    raw = text.encode('utf-8', errors='replace')
    return raw[-limit:].decode('utf-8', errors='ignore'), len(raw) > limit


def exit_status(value, *, snake=False):
    if not isinstance(value, dict):
        return None
    code = value.get('exit_code' if snake else 'exitCode')
    signal = value.get('signal')
    if type(code) is not int and not isinstance(signal, str):
        return None
    return {'exitCode': code if type(code) is int else None,
            'signal': signal if isinstance(signal, str) else None}


def terminal_ids(content):
    return tuple(dict.fromkeys(item['terminalId'] for item in content or []
        if isinstance(item, dict) and item.get('type') == 'terminal'
        and isinstance(item.get('terminalId'), str))) if isinstance(content, list) else ()
