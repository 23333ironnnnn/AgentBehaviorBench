"""Observation must snapshot state before serializing values that can yield."""
import json
import threading
from uuid import uuid4
import pytest

from agentbench.observe.langchain import TraceCallback
from agentbench.observe.store import TraceStore


@pytest.mark.parametrize('nested', [False, True])
def test_chain_end_does_not_crash_when_another_branch_extends_shared_state(tmp_path, nested):
    serialize = threading.Event()
    changed = threading.Event()

    class YieldingValue:
        def __str__(self):
            serialize.set()
            assert changed.wait(3)
            return 'observed value'

    state = {'first': YieldingValue(), 'second': {'nested': 'unchanged'}}

    def branch():
        assert serialize.wait(3)
        state['parallel_branch_result'] = 42
        changed.set()

    thread = threading.Thread(target=branch)
    thread.start()
    try:
        callback = TraceCallback(TraceStore(tmp_path/'framework.jsonl', 'run-1', environ={}))
        callback.on_chain_end({'nested_graph': state} if nested else state, run_id=uuid4())
    finally:
        thread.join(timeout=3)
    record = json.loads((tmp_path/'framework.jsonl').read_text())
    output = record['data']['output']['nested_graph'] if nested else record['data']['output']
    assert output['first']['value'] == 'observed value'
    assert output['second'] == {'nested': 'unchanged'}
    assert 'parallel_branch_result' not in output
    assert state['parallel_branch_result'] == 42


def test_trace_write_failure_is_not_silently_ignored(tmp_path):
    callback = TraceCallback(TraceStore(tmp_path/'absent/framework.jsonl', 'run-1', environ={}))
    assert callback.raise_error
    with pytest.raises(FileNotFoundError):
        callback.on_chain_end({'answer': 'complete'}, run_id=uuid4())
