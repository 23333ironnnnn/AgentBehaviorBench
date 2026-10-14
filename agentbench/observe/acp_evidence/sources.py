"""Bounded snapshots from client callbacks and an explicitly supported extension."""
from agentbench.observe.store import redact
from .contracts import MAX_TERMINALS, bounded_output, exit_status


class TerminalSnapshots:
    source = 'client_observed'

    def __init__(self, secrets=()):
        self.items = {}
        self.secrets = secrets

    def entry(self, identifier):
        if not isinstance(identifier, str) or not identifier:
            return None
        if identifier not in self.items:
            if len(self.items) >= MAX_TERMINALS:
                return None
            self.items[identifier] = {'source': self.source}
        return self.items[identifier]

    def snapshot(self, terminal_id):
        value = self.items.get(terminal_id)
        return dict(value) if value is not None else None

    def on_event(self, name, data):
        if name not in ('terminal_started', 'terminal_output', 'terminal_exited', 'terminal_released'):
            return
        value = self.entry(data.get('terminal_id'))
        if value is None:
            return
        if isinstance(data.get('output'), str):
            output, truncated = bounded_output(redact(data['output'], self.secrets))
            value.update(output=output, truncated=bool(data.get('truncated')) or truncated)
            if truncated:
                value['evidence_truncated'] = True
        status = exit_status(data.get('exitStatus')) or (
            exit_status(data, snake=True) if name == 'terminal_exited' else None)
        if status is not None:
            value['exitStatus'] = status


class MetaTerminalOutput(TerminalSnapshots):
    """Codec for terminal_info/output/exit; unrelated _meta stays opaque."""
    source = 'agent_reported'
    capabilities = {'terminal_output': True}

    def identifiers(self):
        return tuple(self.items)

    def on_update(self, update):
        meta = update.get('_meta')
        if not isinstance(meta, dict):
            return
        info = meta.get('terminal_info')
        if isinstance(info, dict):
            self.entry(info.get('terminal_id'))
        chunk = meta.get('terminal_output')
        if isinstance(chunk, dict) and isinstance(chunk.get('data'), str):
            value = self.entry(chunk.get('terminal_id'))
            if value is not None:
                output, truncated = bounded_output(redact(value.get('output', '') + chunk['data'], self.secrets))
                value.update(output=output, truncated=value.get('truncated', False) or truncated)
                if truncated:
                    value['evidence_truncated'] = True
        end = meta.get('terminal_exit')
        if isinstance(end, dict):
            status = exit_status(end, snake=True)
            value = self.entry(end.get('terminal_id'))
            if value is not None and status is not None:
                value['exitStatus'] = status


# Register extension codecs, rather than branching on an Agent's identity.
TERMINAL_EXTENSIONS = (MetaTerminalOutput,)


def terminal_capabilities():
    return {key: value for codec in TERMINAL_EXTENSIONS for key, value in codec.capabilities.items()}
