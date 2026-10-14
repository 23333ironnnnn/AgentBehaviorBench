"""Resolve terminal content into tool results without rerunning any command."""
from .contracts import TerminalEvidenceSource, terminal_ids
from .sources import TERMINAL_EXTENSIONS


class ToolEvidence:
    def __init__(self, client: TerminalEvidenceSource, *, extensions=TERMINAL_EXTENSIONS, secrets=()):
        self.extensions = [codec(secrets=secrets) for codec in extensions]
        self.sources = [client, *self.extensions]

    def on_update(self, update):
        for extension in self.extensions:
            extension.on_update(update)

    def identifiers(self, fields):
        return tuple(dict.fromkeys((*terminal_ids(fields.get('content')),
            *(key for source in self.extensions for key in source.identifiers()))))

    def terminal(self, identifier):
        # Keep independently observed sources separate: Agent metadata cannot
        # overwrite a client's output or exit code for the same ID.
        snapshots = [value for source in self.sources
                     if (value := source.snapshot(identifier)) is not None]
        if not snapshots:
            return {'output_status': 'not_observed', 'exit_status': 'not_observed'}
        value = dict(snapshots[0])
        value['output_status'] = 'observed' if 'output' in value else 'not_observed'
        value['exit_status'] = 'observed' if 'exitStatus' in value else 'not_observed'
        if len(snapshots) > 1:
            value['additional_sources'] = snapshots[1:]
        return value

    def result(self, fields):
        identifiers = self.identifiers(fields)
        content = fields.get('content')
        if identifiers:
            content = [({**item, **self.terminal(item['terminalId'])}
                        if isinstance(item, dict) and item.get('type') == 'terminal'
                        and isinstance(item.get('terminalId'), str) else item)
                       for item in content] if isinstance(content, list) else []
            embedded = terminal_ids(fields.get('content'))
            content += [{'type': 'terminal', 'terminalId': key, **self.terminal(key)}
                        for key in identifiers if key not in embedded]
            return {'output': {'rawOutput': fields['rawOutput'], 'content': content}
                    if 'rawOutput' in fields else content}
        return ({'output': fields['rawOutput']} if 'rawOutput' in fields else
                {'output': fields['content']} if 'content' in fields else {})
