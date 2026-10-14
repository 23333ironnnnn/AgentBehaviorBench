"""Terminal references must resolve in the evidence actually consumed by KUMA."""
import json
from pathlib import Path
import sys

import pytest

from agentbench.adapter.acp import ACPAdapter
from agentbench.adapter.acp.config import ACPConfig
from agentbench.observe.invocation import InvocationObservation
from tests.sdk_fixtures.issue_run import sdk_run

SERVER = Path(__file__).parent / 'acp_fixtures/server.py'


def tool_result(evidence, call_id):
    span = next(s for s in evidence['spans']
                if s['attributes'].get('gen_ai.tool.call.id') == call_id)
    value = span['attributes']['gen_ai.tool.call.result']
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.parametrize('prompt,call_id,output,code,source', [
    ('terminal-reference', 'terminal-tool', 'SQL syntax error\n', 7, 'client_observed'),
    ('terminal-meta', 'meta-tool', 'first\nsecond\n', 143, 'agent_reported'),
])
def test_real_acp_terminal_result_reaches_sdk(tmp_path, prompt, call_id, output, code, source):
    with sdk_run(tmp_path / 'repo', [prompt]) as (run, provider):
        run.get_input()
        observed = InvocationObservation(tmp_path / 'trace', 'invoke', run.run_id, 'acp', provider=provider)
        observed.store.record('execution_start', input=prompt)
        adapter = ACPAdapter(ACPConfig(tmp_path, (sys.executable, str(SERVER)), str(tmp_path),
                                       timeout=5, cleanup_timeout=.2))
        try:
            result = adapter.invoke(prompt, run_config=observed.config())
            observed.store.record('execution_end', output=result.output)
        finally:
            adapter.close()
            observed.close()
        run.submit(output=result.output)
        evidence = run.history[0].submission.extensions['trace_evidence']
        terminal = tool_result(evidence, call_id)[0]
        assert terminal['output'] == output
        assert terminal['exitStatus']['exitCode'] == code
        assert terminal['source'] == source
        assert terminal['truncated'] is False
        assert 'trace_event_limit' not in evidence['reasons']


def test_meta_chunks_and_exit_reach_sdk_without_losing_other_content(tmp_path):
    with sdk_run(tmp_path / 'repo', ['task']) as (run, provider):
        run.get_input()
        observed = InvocationObservation(tmp_path / 'trace', 'invoke', run.run_id, 'acp', provider=provider)
        observed.store.record('execution_start', input='task')
        callback = observed.callbacks[0]
        updates = [
            {'sessionUpdate': 'tool_call', 'toolCallId': 'meta-tool', 'title': 'bash',
             'status': 'in_progress', 'rawInput': {'command': 'test-command'},
             'content': [{'type': 'terminal', 'terminalId': 'remote-terminal'},
                         {'type': 'content', 'content': {'type': 'text', 'text': 'Explanation'}}]},
            {'sessionUpdate': 'tool_call_update', 'toolCallId': 'meta-tool',
             '_meta': {'terminal_output': {'terminal_id': 'remote-terminal', 'data': 'first\n'}}},
            {'sessionUpdate': 'tool_call_update', 'toolCallId': 'meta-tool',
             '_meta': {'terminal_output': {'terminal_id': 'remote-terminal', 'data': 'second\n'}}},
            {'sessionUpdate': 'tool_call_update', 'toolCallId': 'meta-tool', 'status': 'failed',
             '_meta': {'terminal_exit': {'terminal_id': 'remote-terminal', 'exit_code': 143}}},
        ]
        for update in updates:
            callback.on_acp_event('session_update', {'update': update})
        callback.on_acp_event('prompt_completed', {'stopReason': 'end_turn'})
        observed.store.record('execution_end', output='Command failed')
        observed.close()
        run.submit(output='Command failed')
        evidence = run.history[0].submission.extensions['trace_evidence']
        result = tool_result(evidence, 'meta-tool')
        assert result[0]['output'] == 'first\nsecond\n'
        assert result[0]['exitStatus']['exitCode'] == 143
        assert result[0]['source'] == 'agent_reported'
        assert result[1] == updates[0]['content'][1]


def test_terminal_events_do_not_exhaust_root_span_budget(tmp_path):
    with sdk_run(tmp_path / 'repo', ['task']) as (run, provider):
        run.get_input()
        observed = InvocationObservation(tmp_path / 'trace', 'invoke', run.run_id, 'acp', provider=provider)
        observed.store.record('execution_start', input='task')
        callback = observed.callbacks[0]
        for index in range(50):
            callback.on_acp_event('terminal_output', {'terminal_id': 't', 'output': str(index), 'truncated': False})
        callback.on_acp_event('prompt_completed', {'stopReason': 'end_turn'})
        observed.store.record('execution_end', output='done')
        observed.close()
        run.submit(output='done')
        evidence = run.history[0].submission.extensions['trace_evidence']
        assert 'trace_event_limit' not in evidence['reasons']
        assert len((tmp_path / 'trace/acp-events.jsonl').read_text().splitlines()) == 51


def test_missing_results_and_multiple_ids_are_not_guessed():
    from agentbench.observe.acp_evidence import TerminalSnapshots, ToolEvidence
    source = TerminalSnapshots()
    tool = ToolEvidence(source)
    fields = {'content': [{'type': 'terminal', 'terminalId': 'known'},
                          {'type': 'terminal', 'terminalId': 'unknown'}]}
    source.on_event('terminal_output', {'terminal_id': 'known', 'output': '', 'truncated': False})
    result = tool.result(fields)['output']
    assert result[0]['output'] == '' and result[0]['output_status'] == 'observed'
    assert result[0]['exit_status'] == 'not_observed' and 'exitStatus' not in result[0]
    assert result[1]['output_status'] == 'not_observed' and 'output' not in result[1]
    # Unknown extensions and data without an explicit terminal ID stay opaque.
    tool.on_update({'_meta': {'unrelated': {'data': 'ignore'}, 'terminal_output': {'data': 'no ID'}}})
    assert tool.result(fields)['output'] == result


def test_meta_only_result_preserves_raw_output_and_is_scoped_to_one_tool():
    from agentbench.observe.acp_evidence import TerminalSnapshots, ToolEvidence
    source = TerminalSnapshots()
    first, second = ToolEvidence(source), ToolEvidence(source)
    first.on_update({'_meta': {'terminal_output': {'terminal_id': 'remote', 'data': 'observed'},
                              'terminal_exit': {'terminal_id': 'remote', 'exit_code': 1}}})
    result = first.result({'rawOutput': {'summary': 'original'}})['output']
    assert result['rawOutput'] == {'summary': 'original'}
    assert result['content'][0]['output'] == 'observed'
    assert second.result({'rawOutput': {'summary': 'original'}}) == {'output': {'summary': 'original'}}
    source.on_event('terminal_output', {'terminal_id': 'remote', 'output': 'independent',
                                       'exitStatus': {'exitCode': 0}, 'truncated': False})
    terminal = first.result({})['output'][0]
    assert terminal['source'] == 'client_observed' and terminal['exitStatus']['exitCode'] == 0
    assert terminal['additional_sources'][0]['exitStatus']['exitCode'] == 1


def test_projection_bounds_unicode_and_redacts_before_truncation():
    from agentbench.observe.acp_evidence import TerminalSnapshots, ToolEvidence
    from agentbench.observe.acp_evidence.contracts import MAX_TERMINAL_BYTES
    secret = 'fixture-terminal-secret-123456789'
    source = TerminalSnapshots(secrets=(secret,))
    tool = ToolEvidence(source, secrets=(secret,))
    raw = 'prefix' * MAX_TERMINAL_BYTES + secret + '\u00e9' * (MAX_TERMINAL_BYTES // 2 - 5)
    source.on_event('terminal_output', {'terminal_id': 'client', 'output': raw, 'truncated': False})
    tool.on_update({'_meta': {'terminal_output': {'terminal_id': 'meta', 'data': raw}}})
    for item in (source.snapshot('client'), tool.result({})['output'][0]):
        assert item['truncated'] and item['evidence_truncated']
        assert len(item['output'].encode('utf-8')) <= MAX_TERMINAL_BYTES
        assert secret not in item['output'] and '23456789' not in item['output']


def test_custom_terminal_source_uses_the_same_protocol():
    from agentbench.observe.acp_evidence import ToolEvidence
    class CustomSource:
        def snapshot(self, terminal_id):
            return {'output': terminal_id, 'truncated': False, 'exitStatus': {'exitCode': 0},
                    'source': 'custom_observed'}
    result = ToolEvidence(CustomSource()).result({'content': [{'type': 'terminal', 'terminalId': 'custom'}]})
    assert result['output'][0]['output'] == 'custom'


def test_completed_tool_with_missing_terminal_result_closes_honestly_at_boundary(tmp_path):
    with sdk_run(tmp_path / 'repo', ['task']) as (run, provider):
        run.get_input()
        observed = InvocationObservation(tmp_path / 'trace', 'invoke', run.run_id, 'acp', provider=provider)
        observed.store.record('execution_start', input='task')
        callback = observed.callbacks[0]
        callback.on_acp_event('session_update', {'update': {'sessionUpdate': 'tool_call',
            'toolCallId': 'missing', 'title': 'background command', 'status': 'completed',
            'content': [{'type': 'terminal', 'terminalId': 'unavailable'}]}})
        callback.on_acp_event('prompt_completed', {'stopReason': 'end_turn'})
        observed.store.record('execution_end', output='done')
        observed.close()
        run.submit(output='done')
        result = tool_result(run.history[0].submission.extensions['trace_evidence'], 'missing')[0]
        assert result['output_status'] == 'not_observed' and 'output' not in result
        assert result['exit_status'] == 'not_observed' and 'exitStatus' not in result
        assert json.loads((tmp_path / 'trace/otel-status.json').read_text())['status'] == 'complete'


def test_terminal_source_capacity_is_bounded():
    from agentbench.observe.acp_evidence import TerminalSnapshots
    from agentbench.observe.acp_evidence.contracts import MAX_TERMINALS
    source = TerminalSnapshots()
    for index in range(MAX_TERMINALS + 20):
        source.on_event('terminal_output', {'terminal_id': str(index), 'output': str(index)})
    assert len(source.items) == MAX_TERMINALS
    assert source.snapshot(str(MAX_TERMINALS)) is None
    assert source.snapshot('0')['output'] == '0'


@pytest.mark.parametrize('source', ['client', 'meta'])
def test_final_terminal_snapshot_includes_data_after_tool_completion(tmp_path, source):
    with sdk_run(tmp_path / 'repo', ['task']) as (run, provider):
        run.get_input()
        observed = InvocationObservation(tmp_path / 'trace', 'invoke', run.run_id, 'acp', provider=provider)
        observed.store.record('execution_start', input='task')
        callback = observed.callbacks[0]
        callback.on_acp_event('session_update', {'update': {'sessionUpdate': 'tool_call',
            'toolCallId': 'late', 'title': 'command', 'status': 'in_progress',
            'content': [{'type': 'terminal', 'terminalId': 't'}]}})
        if source == 'client':
            callback.on_acp_event('terminal_output', {'terminal_id': 't', 'output': 'early',
                                                     'exitStatus': {'exitCode': 0}, 'truncated': False})
        else:
            callback.on_acp_event('session_update', {'update': {'sessionUpdate': 'tool_call_update',
                'toolCallId': 'late', '_meta': {'terminal_output': {'terminal_id': 't', 'data': 'early'},
                                               'terminal_exit': {'terminal_id': 't', 'exit_code': 0}}}})
        callback.on_acp_event('session_update', {'update': {'sessionUpdate': 'tool_call_update',
            'toolCallId': 'late', 'status': 'completed'}})
        if source == 'client':
            callback.on_acp_event('terminal_released', {'terminal_id': 't', 'output': 'early final',
                                                       'exitStatus': {'exitCode': 0}, 'truncated': False})
        else:
            callback.on_acp_event('session_update', {'update': {'sessionUpdate': 'tool_call_update',
                'toolCallId': 'late', '_meta': {'terminal_output': {'terminal_id': 't', 'data': ' final'}}}})
        callback.on_acp_event('prompt_completed', {'stopReason': 'end_turn'})
        observed.store.record('execution_end', output='done')
        observed.close()
        run.submit(output='done')
        result = tool_result(run.history[0].submission.extensions['trace_evidence'], 'late')[0]
        assert result['output'] == 'early final'
