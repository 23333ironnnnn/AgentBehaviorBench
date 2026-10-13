"""The worker image must install filelock, which the injected Suite lock imports (#391)."""
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
from packaging.requirements import Requirement

from agentbench.runtime.agentcontainer.config import tomllib
from agentbench.runtime.docker.worker_build import worker_build_context
from tests.test_issue85 import agent_config


def test_worker_stages_filelock_matching_project_dependency(tmp_path):
    config = agent_config(tmp_path)
    with worker_build_context(config) as (context, _dockerfile):
        requirements = context / '.abb-runtime/agentbench/runtime/docker/requirements.txt'
        entries = [Requirement(line) for line in requirements.read_text().splitlines()
                   if line and not line.startswith('#')]
        filelock = next(item for item in entries if item.name == 'filelock')
        assert filelock.marker is None
        project = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())
        assert str(filelock) in [str(Requirement(item)) for item in project['project']['dependencies']]


@pytest.mark.skipif(not os.getenv('ABB_ISSUE391_DOCKER'), reason='Opt-in Docker build')
@pytest.mark.parametrize('version,venv', [('3.10', False), ('3.11', True)])
def test_clean_image_loads_injected_suite_lock(tmp_path, version, venv):
    config = agent_config(tmp_path, version=version, venv=venv)
    tag = f'abb-issue391:{uuid4().hex}'
    output = Path(__file__).parents[1] / 'results/verification/issue391' / f'{version}-venv-{venv}'
    output.mkdir(parents=True, exist_ok=True)
    try:
        with worker_build_context(config) as (context, dockerfile):
            build = subprocess.run(
                ['docker', 'build', '-t', tag, '-f', str(dockerfile), str(context)],
                capture_output=True, text=True, timeout=300)
            (output / 'build.log').write_text(build.stdout + build.stderr)
            assert build.returncode == 0, f'See {output}/build.log'
        # Load only the lock module so unrelated SDK dependencies cannot mask this failure.
        script = (
            'import json,runpy,sys; '
            'module=runpy.run_path("/opt/abb-runtime/agentbench/harness/session/locking.py"); '
            'assert "SuiteLock" in module; '
            'print(json.dumps({"status":"passed","python":sys.version,"executable":sys.executable}))')
        result = subprocess.run(
            ['docker', 'run', '--rm', '--network', 'none', tag, 'python', '-c', script],
            capture_output=True, text=True, timeout=60)
        (output / 'container.log').write_text(result.stdout + result.stderr)
        assert result.returncode == 0, f'See {output}/container.log'
        (output / 'acceptance.json').write_text(json.dumps(json.loads(result.stdout), indent=2))
    finally:
        subprocess.run(['docker', 'image', 'rm', tag], capture_output=True, timeout=60)
