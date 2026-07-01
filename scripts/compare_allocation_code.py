import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

from validate_plan import HELPER_SIGNATURES


@dataclass
class ConsistencyResult:
    status: str
    summary: str
    allocation_actions: list = field(default_factory=list)
    code_actions: list = field(default_factory=list)
    differences: list = field(default_factory=list)

    def to_dict(self):
        return {
            "status": self.status,
            "summary": self.summary,
            "allocation_actions": self.allocation_actions,
            "code_actions": self.code_actions,
            "differences": self.differences,
        }


def compare_allocation_to_code(allocation_plan, code_plan, task_robots, validation_status):
    try:
        allocation_actions = extract_allocation_actions(allocation_plan)
        code_actions = extract_code_actions(code_plan, task_robots)
    except ValueError as exc:
        return ConsistencyResult(
            status="UNKNOWN",
            summary=f"Could not compare allocation and code: {exc}",
            differences=[{"type": "comparison_error", "message": str(exc)}],
        )

    if not allocation_actions:
        return ConsistencyResult(
            status="UNKNOWN",
            summary="Could not compare allocation and code because allocation has no actions.",
            allocation_actions=allocation_actions,
            code_actions=code_actions,
            differences=[{"type": "missing_allocation_actions"}],
        )

    if not code_actions:
        return ConsistencyResult(
            status="UNKNOWN",
            summary="Could not compare allocation and code because code action extraction produced no actions.",
            allocation_actions=allocation_actions,
            code_actions=code_actions,
            differences=[{"type": "missing_code_actions"}],
        )

    differences = compare_action_sequences(allocation_actions, code_actions)
    if not differences:
        return ConsistencyResult(
            status="MATCH",
            summary="Generated code follows the structured allocation action sequence.",
            allocation_actions=allocation_actions,
            code_actions=code_actions,
            differences=[],
        )

    if validation_status == "PASS":
        status = "DEVIATED_BUT_EXECUTABLE"
        summary = "Generated code deviates from structured allocation but passed code validation."
    else:
        status = "DEVIATED_AND_INVALID"
        summary = "Generated code deviates from structured allocation and did not pass code validation."

    return ConsistencyResult(
        status=status,
        summary=summary,
        allocation_actions=allocation_actions,
        code_actions=code_actions,
        differences=differences,
    )


def extract_allocation_actions(allocation_plan):
    if not isinstance(allocation_plan, dict):
        raise ValueError("allocation_plan must be a dict.")

    assignments = allocation_plan.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError("allocation_plan.assignments must be a list.")

    actions = []
    for assignment_index, assignment in enumerate(assignments):
        if not isinstance(assignment, dict):
            continue

        step_id = assignment.get("step_id")
        assignment_actions = assignment.get("actions", [])
        if not isinstance(assignment_actions, list):
            continue

        for action_index, action in enumerate(assignment_actions):
            if not isinstance(action, dict):
                continue
            actions.append(
                {
                    "helper": action.get("helper"),
                    "robot": action.get("robot"),
                    "object": action.get("object"),
                    "receptacle": action.get("receptacle"),
                    "step_id": step_id,
                    "assignment_index": assignment_index,
                    "action_index": action_index,
                }
            )

    return actions


def extract_code_actions(code_plan, task_robots):
    try:
        tree = ast.parse(code_plan)
    except SyntaxError as exc:
        raise ValueError(f"code_plan.py is not valid Python: {exc.msg}") from exc

    extractor = CodeActionExtractor(task_robots)
    return extractor.extract(tree)


def compare_action_sequences(allocation_actions, code_actions):
    differences = []
    allocation_tuples = [_action_tuple(action) for action in allocation_actions]
    code_tuples = [_action_tuple(action) for action in code_actions]

    max_len = max(len(allocation_tuples), len(code_tuples))
    for index in range(max_len):
        allocation_action = allocation_tuples[index] if index < len(allocation_tuples) else None
        code_action = code_tuples[index] if index < len(code_tuples) else None
        if allocation_action != code_action:
            differences.append(
                {
                    "type": "action_mismatch",
                    "index": index,
                    "allocation_action": allocation_action,
                    "code_action": code_action,
                }
            )

    return differences


def write_consistency_outputs(log_path, result):
    log_path = Path(log_path)
    (log_path / "code_actions.json").write_text(
        json.dumps(result.code_actions, indent=2) + "\n"
    )
    (log_path / "allocation_code_consistency_report.json").write_text(
        json.dumps(result.to_dict(), indent=2) + "\n"
    )


def _action_tuple(action):
    return (
        action.get("helper"),
        action.get("robot"),
        action.get("object"),
        action.get("receptacle"),
    )


class CodeActionExtractor:
    def __init__(self, task_robots):
        self.robots = task_robots
        self.functions = {}
        self.actions = []

    def extract(self, tree):
        self.functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }

        env = {"robots": self.robots}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                continue
            self._extract_from_node(node, env)

        return self.actions

    def _extract_from_node(self, node, env):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            self._extract_from_call(node.value, env)
            return

        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            self._extract_from_call(node.value, env)
            return

        for child in ast.iter_child_nodes(node):
            self._extract_from_node(child, env)

    def _extract_from_call(self, call, env):
        fn_name = self._call_name(call)
        if fn_name is None:
            return

        if fn_name in HELPER_SIGNATURES:
            self._append_helper_action(fn_name, call, env)
            return

        if fn_name in self.functions:
            self._extract_from_user_function_call(fn_name, call, env)

    def _extract_from_user_function_call(self, fn_name, call, env):
        fn_def = self.functions[fn_name]
        local_env = dict(env)

        for param, arg in zip(fn_def.args.args, call.args):
            local_env[param.arg] = self._resolve_value(arg, env)

        for stmt in fn_def.body:
            self._extract_from_node(stmt, local_env)

    def _append_helper_action(self, fn_name, call, env):
        robot = self._resolve_robot_name(call.args[0], env) if call.args else None
        obj = self._resolve_object_name(call.args[1], env) if len(call.args) > 1 else None
        receptacle = self._resolve_object_name(call.args[2], env) if len(call.args) > 2 else None

        self.actions.append(
            {
                "helper": fn_name,
                "robot": robot,
                "object": obj,
                "receptacle": receptacle,
                "line": getattr(call, "lineno", 0),
            }
        )

    def _resolve_robot_name(self, node, env):
        value = self._resolve_value(node, env)
        if isinstance(value, dict) and "name" in value:
            return value["name"]
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            return value[0].get("name")
        return None

    def _resolve_object_name(self, node, env):
        value = self._resolve_value(node, env)
        if isinstance(value, str):
            return value
        return None

    def _resolve_value(self, node, env):
        if isinstance(node, ast.Name):
            return env.get(node.id)

        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.List):
            return [self._resolve_value(element, env) for element in node.elts]

        if isinstance(node, ast.Tuple):
            return tuple(self._resolve_value(element, env) for element in node.elts)

        if isinstance(node, ast.Subscript):
            container = self._resolve_value(node.value, env)
            index = self._resolve_index(node.slice, env)
            if container is not None and index is not None:
                try:
                    return container[index]
                except (IndexError, KeyError, TypeError):
                    return None

        return None

    def _resolve_index(self, node, env):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Index):
            return self._resolve_index(node.value, env)
        value = self._resolve_value(node, env)
        if isinstance(value, int):
            return value
        return None

    def _call_name(self, call):
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None
