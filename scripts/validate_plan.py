import argparse
import ast
from dataclasses import dataclass, field
from pathlib import Path


HELPER_SIGNATURES = {
    "GoToObject": 2,
    "PickupObject": 2,
    "PutObject": 3,
    "OpenObject": 2,
    "CloseObject": 2,
    "BreakObject": 2,
    "SliceObject": 2,
    "SwitchOn": 2,
    "SwitchOff": 2,
    "CleanObject": 2,
    "ThrowObject": 2,
}
HELPER_SIGNATURE_TEXT = {
    "GoToObject": "GoToObject(robot, object)",
    "PickupObject": "PickupObject(robot, object)",
    "PutObject": "PutObject(robot, object, receptacleObject)",
    "OpenObject": "OpenObject(robot, object)",
    "CloseObject": "CloseObject(robot, object)",
    "BreakObject": "BreakObject(robot, object)",
    "SliceObject": "SliceObject(robot, object)",
    "SwitchOn": "SwitchOn(robot, object)",
    "SwitchOff": "SwitchOff(robot, object)",
    "CleanObject": "CleanObject(robot, object)",
    "ThrowObject": "ThrowObject(robot, object)",
}

OBJECT_CONSUMING_ACTIONS = {"PutObject", "ThrowObject"}
KNOWN_UNIMPLEMENTED_HELPERS = {
    "DropHandObject",
    "PushObject",
    "PullObject",
    "TransferObject",
}


@dataclass
class ValidationIssue:
    line: int
    severity: str
    message: str
    code: str = "VALIDATION_ERROR"
    suggestion: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class ValidationClassification:
    status: str
    reason: str
    suggestions: list = field(default_factory=list)
    details: dict = field(default_factory=dict)


class PlanValidator:
    def __init__(self, robots):
        self.robots = robots
        self.functions = {}
        self.issues = []
        self.held_objects = {}

    def validate(self, tree):
        self.functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }

        env = {"robots": self.robots}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                continue
            self._validate_node(node, env)

        return self.issues

    def _validate_node(self, node, env):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            self._validate_call(node.value, env)
            return

        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            self._validate_call(node.value, env)
            return

        for child in ast.iter_child_nodes(node):
            self._validate_node(child, env)

    def _validate_call(self, call, env):
        fn_name = self._call_name(call)
        if fn_name is None:
            return

        if fn_name in HELPER_SIGNATURES:
            self._validate_helper_call(fn_name, call, env)
            return

        if fn_name in self.functions:
            self._validate_user_function_call(fn_name, call, env)
            return

        if fn_name in KNOWN_UNIMPLEMENTED_HELPERS or fn_name.endswith("Object"):
            self._add(
                call.lineno,
                "error",
                f"{fn_name} is not an implemented helper function.",
                code="UNIMPLEMENTED_HELPER",
                suggestion="Use only implemented helpers, or implement this helper before exposing it in the action list.",
                details={"helper": fn_name},
            )

    def _validate_user_function_call(self, fn_name, call, env):
        fn_def = self.functions[fn_name]
        local_env = dict(env)

        for param, arg in zip(fn_def.args.args, call.args):
            local_env[param.arg] = self._resolve_value(arg, env)

        for stmt in fn_def.body:
            self._validate_node(stmt, local_env)

    def _validate_helper_call(self, fn_name, call, env):
        expected_arg_count = HELPER_SIGNATURES[fn_name]
        if len(call.args) != expected_arg_count:
            self._add(
                call.lineno,
                "error",
                f"{fn_name} expects {expected_arg_count} arguments, but {len(call.args)} were provided.",
                code="HELPER_SIGNATURE_MISMATCH",
                suggestion=f"Use the implemented signature: {HELPER_SIGNATURE_TEXT[fn_name]}.",
                details={
                    "helper": fn_name,
                    "expected_args": expected_arg_count,
                    "actual_args": len(call.args),
                },
            )
            return

        robots = self._resolve_robots(call.args[0], env)
        object_name = self._resolve_object_name(call.args[1], env)

        if robots is None:
            self._add(
                call.lineno,
                "warning",
                f"Could not resolve robot argument for {fn_name}.",
                code="UNRESOLVED_ROBOT_ARGUMENT",
                suggestion="Use explicit robots, robot_list entries, or robots entries so validation can resolve the actor.",
                details={"helper": fn_name},
            )
            return

        if not robots:
            self._add(
                call.lineno,
                "error",
                f"{fn_name} has no resolvable robot target.",
                code="NO_ROBOT_TARGET",
                suggestion="Pass a valid robot object as the first helper argument.",
                details={"helper": fn_name},
            )
            return

        if fn_name in {"PutObject", "ThrowObject", "OpenObject", "CloseObject", "BreakObject", "SliceObject", "SwitchOn", "SwitchOff", "CleanObject"} and len(robots) != 1:
            self._add(
                call.lineno,
                "error",
                f"{fn_name} must target exactly one robot, but {len(robots)} were resolved.",
                code="MULTI_ROBOT_UNSUPPORTED_FOR_HELPER",
                suggestion=f"Call {fn_name} with exactly one robot. Use separate calls if each robot must act.",
                details={"helper": fn_name, "robot_count": len(robots)},
            )
            return

        for robot in robots:
            self._validate_robot_skill(call.lineno, robot, fn_name)

        if object_name is None:
            return

        if fn_name == "PickupObject":
            for robot in robots:
                held = self.held_objects.get(robot["name"])
                if held is not None:
                    self._add(
                        call.lineno,
                        "error",
                        f"{robot['name']} tries to pick up {object_name}, but is already holding {held}.",
                        code="HAND_NOT_EMPTY",
                        suggestion=(
                            f"{robot['name']} is already holding {held}. Remove or reorder "
                            f"PickupObject({object_name}) unless {object_name} must be moved or held for the goal."
                        ),
                        details={
                            "actor": robot["name"],
                            "held_object": held,
                            "target_object": object_name,
                        },
                    )
                else:
                    self.held_objects[robot["name"]] = object_name

        if fn_name in OBJECT_CONSUMING_ACTIONS:
            robot = robots[0]
            held = self.held_objects.get(robot["name"])
            if held != object_name:
                holder = self._find_holder(object_name)
                suggestion = self._ownership_suggestion(fn_name, object_name, robot, holder)
                self._add(
                    call.lineno,
                    "error",
                    f"{robot['name']} tries to {fn_name} {object_name}, but is holding {held or 'nothing'}.",
                    code="OBJECT_OWNERSHIP_MISMATCH",
                    suggestion=suggestion,
                    details={
                        "action": fn_name,
                        "actor": robot["name"],
                        "object": object_name,
                        "actor_holding": held,
                        "current_holder": holder["name"] if holder else None,
                    },
                )
            else:
                self.held_objects[robot["name"]] = None

    def _validate_robot_skill(self, line, robot, fn_name):
        skills = robot.get("skills", [])
        if fn_name not in skills:
            candidates = self._robots_with_skill(fn_name)
            self._add(
                line,
                "error",
                f"{robot['name']} does not have required skill {fn_name}.",
                code="ROBOT_SKILL_MISMATCH",
                suggestion=self._skill_suggestion(fn_name, candidates),
                details={
                    "actor": robot["name"],
                    "required_skill": fn_name,
                    "candidate_robots": [candidate["name"] for candidate in candidates],
                },
            )

    def _resolve_robots(self, node, env):
        value = self._resolve_value(node, env)
        if isinstance(value, dict) and "name" in value:
            return [value]
        if isinstance(value, list):
            robots = []
            for item in value:
                if isinstance(item, dict) and "name" in item:
                    robots.append(item)
                else:
                    return None
            return robots
        return None

    def _resolve_value(self, node, env):
        if isinstance(node, ast.Name):
            return env.get(node.id)

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

        if isinstance(node, ast.Constant):
            return node.value

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

    def _resolve_object_name(self, node, env):
        value = self._resolve_value(node, env)
        if isinstance(value, str):
            return value
        return None

    def _call_name(self, call):
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    def _robots_with_skill(self, skill_name):
        return [
            robot
            for robot in self.robots
            if skill_name in robot.get("skills", [])
        ]

    def _find_holder(self, object_name):
        for robot_name, held_object in self.held_objects.items():
            if held_object == object_name:
                for robot in self.robots:
                    if robot["name"] == robot_name:
                        return robot
        return None

    def _ownership_suggestion(self, fn_name, object_name, actor, holder):
        if holder is None:
            return f"Make {actor['name']} pick up {object_name} before calling {fn_name}, or keep the object action on the actual holder."

        if fn_name in holder.get("skills", []):
            return f"Use {holder['name']} for {fn_name}({object_name}), or add an explicit TransferObject helper before this action."

        return f"{holder['name']} holds {object_name}, but lacks {fn_name}. This plan requires object handoff; add a TransferObject helper or mark the plan infeasible."

    def _skill_suggestion(self, fn_name, candidates):
        if not candidates:
            return f"No available robot has {fn_name}; mark the plan infeasible or change the decomposition."
        names = ", ".join(candidate["name"] for candidate in candidates)
        return f"Assign this action to a robot with {fn_name}: {names}."

    def _add(self, line, severity, message, code="VALIDATION_ERROR", suggestion="", details=None):
        self.issues.append(
            ValidationIssue(
                line=line,
                severity=severity,
                message=message,
                code=code,
                suggestion=suggestion,
                details=details or {},
            )
        )


def load_robots_from_log(log_path):
    for line in log_path.read_text().splitlines():
        if line.startswith("robots = "):
            return ast.literal_eval(line.removeprefix("robots = "))
    raise ValueError(f"Could not find robots list in {log_path}")


def load_ground_truth_from_log(log_path):
    for line in log_path.read_text().splitlines():
        if line.startswith("ground_truth = "):
            return ast.literal_eval(line.removeprefix("ground_truth = "))
    return []


def validate_log_plan(log_path):
    log_path = Path(log_path)
    code_path = log_path / "code_plan.py"
    robots = load_robots_from_log(log_path / "log.txt")

    try:
        tree = ast.parse(code_path.read_text(), filename=str(code_path))
    except SyntaxError as exc:
        return [
            ValidationIssue(
                exc.lineno or 0,
                "error",
                f"SyntaxError: {exc.msg}",
                code="PYTHON_SYNTAX_ERROR",
                suggestion="Regenerate code_plan.py as valid Python code before execution.",
            )
        ]

    return PlanValidator(robots).validate(tree)


def classify_validation_result(log_path, issues):
    errors = [issue for issue in issues if issue.severity == "error"]
    if not errors:
        return ValidationClassification(
            status="PASS",
            reason="Plan validation passed.",
        )

    log_path = Path(log_path)
    robots = load_robots_from_log(log_path / "log.txt")
    ground_truth = load_ground_truth_from_log(log_path / "log.txt")
    repair_suggestions = []
    infeasible_reasons = []

    for issue in errors:
        repair = _repair_hint_for_issue(issue, robots, ground_truth)
        if repair is None:
            infeasible_reasons.append(issue.message)
        else:
            repair_suggestions.append(repair)

    if infeasible_reasons:
        return ValidationClassification(
            status="INFEASIBLE",
            reason="At least one validation error cannot be repaired under the current helper and robot skill constraints.",
            suggestions=repair_suggestions,
            details={"infeasible_reasons": infeasible_reasons},
        )

    return ValidationClassification(
        status="REPAIRABLE_PLAN_ERROR",
        reason="The task may be executable, but the generated code plan violates helper, skill, or object ownership constraints.",
        suggestions=repair_suggestions,
    )


def _repair_hint_for_issue(issue, robots, ground_truth):
    if issue.code == "HAND_NOT_EMPTY":
        target_object = issue.details.get("target_object")
        held_object = issue.details.get("held_object")
        actor_name = issue.details.get("actor")

        contains_goal = _find_contains_goal(ground_truth, target_object)
        state_goal = _find_state_goal(ground_truth, target_object)
        if state_goal and not contains_goal:
            return (
                f"Remove unnecessary PickupObject({target_object}). The final goal changes "
                f"{target_object}'s state to {state_goal.get('state')}, and does not require "
                f"moving it into a receptacle or holding it."
            )

        if held_object and actor_name:
            return (
                f"{actor_name} is already holding {held_object}. Put or throw {held_object} "
                f"before picking up {target_object}, or reorder the plan so the held object is handled first."
            )

        return issue.suggestion or issue.message

    if issue.code in {
        "PYTHON_SYNTAX_ERROR",
        "HELPER_SIGNATURE_MISMATCH",
        "NO_ROBOT_TARGET",
        "MULTI_ROBOT_UNSUPPORTED_FOR_HELPER",
        "UNRESOLVED_ROBOT_ARGUMENT",
    }:
        return issue.suggestion or issue.message

    if issue.code == "UNIMPLEMENTED_HELPER":
        return issue.suggestion or "Replace the unimplemented helper with implemented helper calls."

    if issue.code == "ROBOT_SKILL_MISMATCH":
        candidates = issue.details.get("candidate_robots", [])
        if candidates:
            return issue.suggestion
        return None

    if issue.code == "OBJECT_OWNERSHIP_MISMATCH":
        action = issue.details.get("action")
        object_name = issue.details.get("object")
        holder_name = issue.details.get("current_holder")
        actor_name = issue.details.get("actor")
        holder = _find_robot(robots, holder_name)
        actor = _find_robot(robots, actor_name)

        if holder and action in holder.get("skills", []):
            return issue.suggestion

        contains_goal = _find_contains_goal(ground_truth, object_name)
        if contains_goal and holder and "PutObject" in holder.get("skills", []):
            receptacle = contains_goal["name"]
            return (
                f"Goal alternative available: the goal is {receptacle} contains {object_name}. "
                f"Use PutObject({holder['name']}, {object_name}, {receptacle}) instead of requiring {action} or handoff."
            )

        if action == "PutObject" and holder is None:
            if contains_goal and actor and "PickupObject" in actor.get("skills", []):
                receptacle = contains_goal["name"]
                return f"Make {actor['name']} pick up {object_name} before PutObject({actor['name']}, {object_name}, {receptacle})."

            state_goal = _find_state_goal(ground_truth, object_name)
            if state_goal:
                return (
                    f"{object_name} has a state goal ({state_goal.get('state')}) rather than a containment goal. "
                    f"Remove the unnecessary PutObject({object_name}) call or pick up the object before putting it."
                )

            if actor and "PickupObject" in actor.get("skills", []):
                return f"Make {actor['name']} pick up {object_name} before calling {action}."

        return None

    return None


def _find_robot(robots, robot_name):
    for robot in robots:
        if robot.get("name") == robot_name:
            return robot
    return None


def _find_contains_goal(ground_truth, object_name):
    for goal in ground_truth:
        if object_name in goal.get("contains", []):
            return goal
    return None


def _find_state_goal(ground_truth, object_name):
    for goal in ground_truth:
        if goal.get("name") == object_name and goal.get("state") not in (None, "None"):
            return goal
    return None


def format_validation_report(issues, classification=None):
    if not issues:
        if classification is None:
            classification = ValidationClassification("PASS", "Plan validation passed.")
        return f"Validation status: {classification.status}\n{classification.reason}"

    if classification is None:
        classification = ValidationClassification(
            status="FAILED",
            reason="Plan validation failed.",
        )

    lines = [
        f"Validation status: {classification.status}",
        classification.reason,
        "Plan validation failed:",
    ]
    for issue in issues:
        lines.append(f"- line {issue.line}: [{issue.severity}] {issue.code}: {issue.message}")
        if issue.suggestion:
            lines.append(f"  suggestion: {issue.suggestion}")
    if classification.suggestions:
        lines.append("Repair suggestions:")
        for suggestion in classification.suggestions:
            lines.append(f"- {suggestion}")
    if classification.details.get("infeasible_reasons"):
        lines.append("Infeasible reasons:")
        for reason in classification.details["infeasible_reasons"]:
            lines.append(f"- {reason}")
    return "\n".join(lines)


def format_issues(issues):
    return format_validation_report(issues)


def print_issues(issues):
    print(format_validation_report(issues))


def write_validation_report(log_path, issues, classification=None):
    report_path = Path(log_path) / "validation_report.txt"
    if classification is None:
        classification = classify_validation_result(log_path, issues)
    report_path.write_text(format_validation_report(issues, classification) + "\n")
    return report_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", type=str, required=True)
    args = parser.parse_args()

    issues = validate_log_plan(args.log_dir)
    classification = classify_validation_result(args.log_dir, issues)
    print(format_validation_report(issues, classification))
    write_validation_report(args.log_dir, issues, classification)

    if any(issue.severity == "error" for issue in issues):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
