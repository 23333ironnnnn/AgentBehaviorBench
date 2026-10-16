"""Runtime paths shared by trusted SDK controllers and isolated Agents."""
from dataclasses import dataclass
from pathlib import Path

REQUESTS = '/run/abb-channel/request'
RESPONSES = '/run/abb-channel/response'
STORAGE = '/run/abb-sdk-storage'


@dataclass(frozen=True)
class ResponseLimits:
    """Per-invocation copying budget, independent of SDK evidence retention."""
    max_bytes: int = 1024 * 1024 * 1024
    max_files: int = 20000

    def __post_init__(self):
        for name in ('max_bytes', 'max_files'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f'Agent response {name} must be a positive integer')

    @classmethod
    def from_environ(cls, environ):
        values = {}
        for field, key in (('max_bytes', 'ABB_AGENT_RESPONSE_MAX_BYTES'),
                           ('max_files', 'ABB_AGENT_RESPONSE_MAX_FILES')):
            if key not in environ:
                continue
            try:
                value = int(environ[key])
                if value <= 0:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError(f'{key} must be a positive integer') from None
            values[field] = value
        return cls(**values)


@dataclass(frozen=True)
class ExecutionChannel:
    requests: Path
    responses: Path

    @classmethod
    def create(cls, directory):
        root = Path(directory)
        paths = [root / 'request', root / 'response']
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)
        return cls(*paths)

    def mounts(self, *, controller=False):
        return ('--mount', f'type=bind,source={self.requests},target={REQUESTS}' + ('' if controller else ',readonly'),
                '--mount', f'type=bind,source={self.responses},target={RESPONSES}')
