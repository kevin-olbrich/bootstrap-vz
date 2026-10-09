import logging
import os.path
import random
import subprocess
import sys

import pytest

import bootstrapvz
from bootstrapvz.base.task import Task
from bootstrapvz.base.tasklist import create_list, topological_sort
from bootstrapvz.common import phases
from bootstrapvz.common.exceptions import TaskListError

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/trixie-openvox.yaml')

# Prints the tasks a dry run of the manifest given as the first argument steps through
DRY_RUN_TASKS = '''
import sys
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import TaskList, load_tasks
manifest = Manifest(path=sys.argv[1])
tasklist = TaskList(load_tasks('resolve_tasks', manifest))
tasklist.run(info=DictClass(manifest=manifest), dry_run=True)
print('\\n'.join(map(str, tasklist.tasks_completed)))
'''


def dry_run_tasks(hash_seed):
    # Import the same bootstrapvz package as this test does (-P keeps the current directory out of sys.path)
    env = dict(os.environ,
               PYTHONHASHSEED=hash_seed,
               PYTHONPATH=os.path.dirname(os.path.dirname(bootstrapvz.__file__)))
    result = subprocess.run([sys.executable, '-P', '-c', DRY_RUN_TASKS, example],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_task_order_is_identical_between_runs():
    orders = [dry_run_tasks(hash_seed) for hash_seed in ['0', '1', '2']]
    assert orders[0].count('\n') > 50
    assert orders[1] == orders[0]
    assert orders[2] == orders[0]


class Validate(Task):
    phase = phases.validation


class Mike(Task):
    phase = phases.preparation


class Zulu(Task):
    phase = phases.preparation


class Alpha(Task):
    phase = phases.preparation
    predecessors = [Zulu]


class Bravo(Task):
    phase = phases.preparation


class Yankee(Task):
    phase = phases.preparation
    successors = [Bravo]


class Unused(Task):
    phase = phases.preparation


class Cleanup(Task):
    phase = phases.cleaning


def test_tasks_are_ordered_by_phase_and_constraints_and_filtered():
    taskset = {Validate, Mike, Zulu, Alpha, Bravo, Yankee, Cleanup}
    # Phases and predecessors/successors come first, names only break ties
    assert create_list(taskset, taskset | {Unused}) == [Validate, Mike, Yankee, Bravo, Zulu, Alpha, Cleanup]


@pytest.mark.parametrize('seed', range(20))
def test_ready_tasks_are_picked_by_name(seed):
    edges = {Validate: [Mike, Zulu, Alpha, Bravo, Yankee, Unused],
             Mike: [], Zulu: [Alpha], Alpha: [], Bravo: [], Yankee: [Bravo], Unused: []}
    # Shuffle the order of the tasks in the graph and of their successors
    rng = random.Random(seed)
    graph = {task: rng.sample(edges[task], len(edges[task])) for task in rng.sample(list(edges), len(edges))}
    assert topological_sort(graph) == [Validate, Mike, Unused, Yankee, Bravo, Zulu, Alpha]


class CycleFirst(Task):
    phase = phases.preparation


class CycleSecond(Task):
    phase = phases.preparation
    predecessors = [CycleFirst]


CycleFirst.predecessors = [CycleSecond]


def test_cycle_is_reported(caplog):
    caplog.set_level(logging.DEBUG, logger='bootstrapvz.base.tasklist')
    tasks = {Validate, CycleFirst, CycleSecond}
    with pytest.raises(TaskListError, match='1 cycles were found'):
        create_list(tasks, tasks)
    cycles = [record.getMessage() for record in caplog.records if record.getMessage().startswith('Cycle: ')]
    assert len(cycles) == 1
    assert sorted(cycles[0][len('Cycle: '):].split(', ')) == [str(CycleFirst), str(CycleSecond)]
