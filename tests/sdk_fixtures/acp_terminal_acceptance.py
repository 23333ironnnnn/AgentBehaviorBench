"""Offline container acceptance: real ACP peer, commands, SDK trace and Judge."""
import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from kuma import create_run
from kuma.otel import configure_trace_evidence
from opentelemetry.sdk.trace import TracerProvider
from agentbench.runtime.agentcontainer.session import AgentSession
from agentbench.runtime.agentcontainer.worker import execute
from agentbench.sdk.plugin.kuma.runner import drive_run
from agentbench.sdk.plugin.kuma.compatibility import run_case
from agentbench.sdk.common.artifacts import Artifacts
from tests.sdk_fixtures.issue_run import profile


def tool_result(evidence, call_id):
    span = next(item for item in evidence['spans']
                if item['attributes'].get('gen_ai.tool.call.id') == call_id)
    value = span['attributes']['gen_ai.tool.call.result']
    return json.loads(value) if isinstance(value, str) else value


async def main(output):
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='abb-terminal-') as temporary:
        repo = Path(temporary)
        server = Path(__file__).resolve().parents[1] / 'acp_fixtures/server.py'
        (repo / 'agent.toml').write_text(
            'agent_id="terminal-fixture"\nframework="acp"\n[runtime]\ntimeout_sec=10\n'
            '[adapter]\ntype="acp"\ncwd="' + str(repo) + '"\ncommand='
            + json.dumps([sys.executable, str(server)]) + '\n')
        provider = TracerProvider()
        capture = configure_trace_evidence(provider)
        checked = []
        def judge(context):
            for history, call, expected, code, source in zip(context.history,
                    ['terminal-tool', 'meta-tool'], ['SQL syntax error\n', 'first\nsecond\n'],
                    [7, 143], ['client_observed', 'agent_reported']):
                evidence = history.submission.extensions['trace_evidence']
                terminal = tool_result(evidence, call)[0]
                assert terminal['output'] == expected
                assert terminal['exitStatus']['exitCode'] == code
                assert terminal['source'] == source
                assert 'trace_event_limit' not in evidence['reasons']
                checked.append(call)
            assert len(checked) == 2
            return {'status': 'pass', 'issues': [],
                    'summary': 'Offline custom Judge verified command output and exit status from both ACP paths.'}
        run = create_run(repo_path=repo, agent_profile_path=profile(repo),
            case_provider=lambda _: {'case_id': 'terminal-case',
                'inputs': ['terminal-reference', 'terminal-meta']},
            judge_provider=judge, track_files=False, allow_local=True, trace_evidence=capture, max_steps=2)
        files = Artifacts(output, environ={})
        files.save('case.json', run_case(run))
        session = AgentSession()
        async def invoke(payload, folder, shared_provider):
            Artifacts(folder, environ={}).save('request.json', {'schema': 'abb.invocation.v1',
                'run_id': uuid4().hex, 'session_id': run.run_id,
                'agent_id': 'terminal-fixture', 'framework': 'acp', 'input': payload})
            await execute(repo, folder / 'request.json', folder, provider=shared_provider, session=session)
            return json.loads((folder / 'result.json').read_text())
        try:
            summary = await drive_run(run, invoke, output, provider=provider)
            assert summary['judge'] == 'received' and summary['otel'] == 'complete', summary
            assert summary['execution'] == 'succeeded' and len(summary['steps']) == 2
            files.save('verification.json', {'status': 'passed', 'sdk_version': version('kuma-defuzex'),
                'production_services_called': False, 'network': 'none', 'provider_mode': 'offline-custom',
                'native_transport': 'real ACP stdio subprocess', 'judge_checked': checked})
            print('Judge received real terminal output and exit status for:', ', '.join(checked), flush=True)
        finally:
            await session.aclose()
            provider.shutdown()
            if run.state in ('ready', 'input_delivered'):
                run.cancel()


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1])))
