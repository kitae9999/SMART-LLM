import json
from dataclasses import dataclass, field
from pathlib import Path

from validate_plan import HELPER_SIGNATURES, OBJECT_CONSUMING_ACTIONS


@dataclass
class AllocationIssue:
    severity: str
    code: str
    message: str
    path: str = ""
    suggestion: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class AllocationValidationResult:
    status: str
    feasibility: str
    reason: str
    issues: list = field(default_factory=list)

    def to_dict(self):
        return {
            "status": self.status,
            "feasibility": self.feasibility,
            "reason": self.reason,
            "issues": [
                {
                    "severity": issue.severity,
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.path,
                    "suggestion": issue.suggestion,
                    "details": issue.details,
                }
                for issue in self.issues
            ],
        }


def validate_allocation_plan(allocation_plan, task_robots):
    issues = []
    robots_by_name = {
        robot.get("name"): robot
        for robot in task_robots
        if isinstance(robot, dict) and robot.get("name")
    }
    held_objects = {name: None for name in robots_by_name}

    assignments = _extract_assignments(allocation_plan, issues)
    if assignments is None:
        return _classify(issues)

    step_ids = _validate_step_ids(assignments, issues)
    _validate_dependencies(assignments, step_ids, issues)

    for assignment_index, assignment in enumerate(assignments):
        assignment_path = f"assignments[{assignment_index}]"
        assignment_robot = assignment.get("robot")
        if assignment_robot not in robots_by_name:
            _add(
                issues,
                "error",
                "UNKNOWN_ROBOT",
                f"{assignment_robot!r} is not one of the runtime robots.",
                f"{assignment_path}.robot",
                details={"robot": assignment_robot},
            )

        actions = assignment.get("actions")
        if not isinstance(actions, list):
            _add(
                issues,
                "error",
                "ALLOCATION_PARSE_ERROR",
                "assignment.actions must be a list.",
                f"{assignment_path}.actions",
            )
            continue

        for action_index, action in enumerate(actions):
            action_path = f"{assignment_path}.actions[{action_index}]"
            if not isinstance(action, dict):
                _add(
                    issues,
                    "error",
                    "ALLOCATION_PARSE_ERROR",
                    "action must be an object.",
                    action_path,
                )
                continue

            _validate_action(action, action_path, robots_by_name, held_objects, issues)

    return _classify(issues)


def format_allocation_validation_report(result):
    lines = [
        f"Allocation validation status: {result.status}",
        f"Feasibility: {result.feasibility}",
        result.reason,
    ]

    if result.issues:
        lines.append("Allocation validation issues:")
        for issue in result.issues:
            path = f"{issue.path}: " if issue.path else ""
            lines.append(f"- [{issue.severity}] {issue.code}: {path}{issue.message}")
            if issue.suggestion:
                lines.append(f"  suggestion: {issue.suggestion}")

    return "\n".join(lines)


def write_allocation_validation_outputs(log_path, result):
    log_path = Path(log_path)
    (log_path / "allocation_validation_report.txt").write_text(
        format_allocation_validation_report(result) + "\n"
    )
    (log_path / "allocation_validation_result.json").write_text(
        json.dumps(result.to_dict(), indent=2) + "\n"
    )


def _extract_assignments(allocation_plan, issues):
    if not isinstance(allocation_plan, dict):
        _add(
            issues,
            "error",
            "ALLOCATION_PARSE_ERROR",
            "allocation_plan must be a JSON object.",
            "$",
        )
        return None

    assignments = allocation_plan.get("assignments")
    if not isinstance(assignments, list):
        _add(
            issues,
            "error",
            "ALLOCATION_PARSE_ERROR",
            "allocation_plan.assignments must be a list.",
            "assignments",
        )
        return None

    return assignments


def _validate_step_ids(assignments, issues):
    seen = set()
    step_ids = set()
    for index, assignment in enumerate(assignments):
        path = f"assignments[{index}].step_id"
        if not isinstance(assignment, dict):
            _add(
                issues,
                "error",
                "ALLOCATION_PARSE_ERROR",
                "assignment must be an object.",
                f"assignments[{index}]",
            )
            continue

        step_id = assignment.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip():
            _add(
                issues,
                "error",
                "ALLOCATION_PARSE_ERROR",
                "step_id must be a non-empty string.",
                path,
            )
            continue

        if step_id in seen:
            _add(
                issues,
                "error",
                "DUPLICATE_STEP_ID",
                f"Duplicate step_id {step_id!r}.",
                path,
                details={"step_id": step_id},
            )
        seen.add(step_id)
        step_ids.add(step_id)

    return step_ids


def _validate_dependencies(assignments, step_ids, issues):
    graph = {}
    for index, assignment in enumerate(assignments):
        if not isinstance(assignment, dict):
            continue

        step_id = assignment.get("step_id")
        depends_on = assignment.get("depends_on", [])
        path = f"assignments[{index}].depends_on"
        if not isinstance(depends_on, list):
            _add(
                issues,
                "error",
                "ALLOCATION_PARSE_ERROR",
                "depends_on must be a list.",
                path,
            )
            continue

        if isinstance(step_id, str):
            graph[step_id] = []
        for dependency in depends_on:
            if dependency not in step_ids:
                _add(
                    issues,
                    "error",
                    "UNKNOWN_DEPENDENCY",
                    f"depends_on references unknown step_id {dependency!r}.",
                    path,
                    details={"dependency": dependency},
                )
            elif isinstance(step_id, str):
                graph[step_id].append(dependency)

    _validate_no_dependency_cycle(graph, issues)


def _validate_no_dependency_cycle(graph, issues):
    visiting = set()
    visited = set()

    def visit(step_id, trail):
        if step_id in visiting:
            cycle = trail[trail.index(step_id):] + [step_id]
            _add(
                issues,
                "error",
                "DEPENDENCY_CYCLE",
                "Allocation dependencies contain a cycle: " + " -> ".join(cycle),
                "assignments",
                details={"cycle": cycle},
            )
            return
        if step_id in visited:
            return

        visiting.add(step_id)
        for dependency in graph.get(step_id, []):
            visit(dependency, trail + [dependency])
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in graph:
        visit(step_id, [step_id])


def _validate_action(action, action_path, robots_by_name, held_objects, issues):
    helper = action.get("helper")
    robot_name = action.get("robot")
    object_name = action.get("object")

    if helper not in HELPER_SIGNATURES:
        _add(
            issues,
            "error",
            "UNKNOWN_HELPER",
            f"{helper!r} is not an implemented helper.",
            f"{action_path}.helper",
            details={"helper": helper},
        )
        return

    robot = robots_by_name.get(robot_name)
    if robot is None:
        _add(
            issues,
            "error",
            "UNKNOWN_ROBOT",
            f"{robot_name!r} is not one of the runtime robots.",
            f"{action_path}.robot",
            details={"robot": robot_name},
        )
        return

    _validate_robot_skill(helper, robot, robots_by_name, action_path, issues)

    if not isinstance(object_name, str) or not object_name.strip():
        _add(
            issues,
            "error",
            "ALLOCATION_PARSE_ERROR",
            "action.object must be a non-empty string.",
            f"{action_path}.object",
        )
        return

    if helper == "PickupObject":
        _validate_pickup(robot_name, object_name, held_objects, action_path, issues)
    elif helper in OBJECT_CONSUMING_ACTIONS:
        _validate_consuming_action(helper, robot_name, object_name, robots_by_name, held_objects, action_path, issues)


def _validate_robot_skill(helper, robot, robots_by_name, action_path, issues):
    if helper in robot.get("skills", []):
        return

    candidates = [
        name
        for name, candidate in robots_by_name.items()
        if helper in candidate.get("skills", [])
    ]
    severity = "error"
    code = "ROBOT_SKILL_MISMATCH"
    suggestion = (
        "Assign this action to a robot with "
        f"{helper}: {', '.join(candidates)}."
        if candidates
        else f"No available robot has {helper}; mark the allocation infeasible or change the decomposition."
    )
    _add(
        issues,
        severity,
        code,
        f"{robot['name']} does not have required skill {helper}.",
        f"{action_path}.robot",
        suggestion=suggestion,
        details={
            "actor": robot["name"],
            "required_skill": helper,
            "candidate_robots": candidates,
        },
    )


def _validate_pickup(robot_name, object_name, held_objects, action_path, issues):
    current_holder = _find_holder(object_name, held_objects)
    if current_holder and current_holder != robot_name:
        _add(
            issues,
            "error",
            "UNSUPPORTED_HANDOFF_REQUIRED",
            f"{robot_name} tries to pick up {object_name}, but {current_holder} is already holding it.",
            action_path,
            suggestion="Keep dependent object manipulation on one robot or implement TransferObject.",
            details={
                "actor": robot_name,
                "object": object_name,
                "current_holder": current_holder,
            },
        )
        return

    held = held_objects.get(robot_name)
    if held is not None and held != object_name:
        _add(
            issues,
            "error",
            "HAND_NOT_EMPTY",
            f"{robot_name} tries to pick up {object_name}, but is already holding {held}.",
            action_path,
            suggestion=f"Put or throw {held} before picking up {object_name}, or reorder the allocation.",
            details={
                "actor": robot_name,
                "held_object": held,
                "target_object": object_name,
            },
        )
        return

    held_objects[robot_name] = object_name


def _validate_consuming_action(helper, robot_name, object_name, robots_by_name, held_objects, action_path, issues):
    held = held_objects.get(robot_name)
    if held == object_name:
        held_objects[robot_name] = None
        return

    holder = _find_holder(object_name, held_objects)
    if holder:
        holder_robot = robots_by_name.get(holder, {})
        if helper in holder_robot.get("skills", []):
            suggestion = f"Use {holder} for {helper}({object_name}) or implement TransferObject."
        else:
            suggestion = (
                f"{holder} holds {object_name}, but lacks {helper}. "
                "This allocation requires unsupported handoff."
            )

        _add(
            issues,
            "error",
            "UNSUPPORTED_HANDOFF_REQUIRED",
            f"{robot_name} tries to {helper} {object_name}, but {holder} is holding it.",
            action_path,
            suggestion=suggestion,
            details={
                "action": helper,
                "actor": robot_name,
                "object": object_name,
                "current_holder": holder,
                "actor_holding": held,
            },
        )
        return

    _add(
        issues,
        "error",
        "OBJECT_NOT_HELD",
        f"{robot_name} tries to {helper} {object_name}, but no robot is holding it.",
        action_path,
        suggestion=f"Make {robot_name} pick up {object_name} before calling {helper}.",
        details={
            "action": helper,
            "actor": robot_name,
            "object": object_name,
            "actor_holding": held,
        },
    )


def _find_holder(object_name, held_objects):
    for robot_name, held_object in held_objects.items():
        if held_object == object_name:
            return robot_name
    return None


def _classify(issues):
    errors = [issue for issue in issues if issue.severity == "error"]
    if not errors:
        return AllocationValidationResult(
            status="ALLOCATION_PASS",
            feasibility="executable",
            reason="Allocation validation passed.",
            issues=issues,
        )

    if any(_is_parse_error(issue) for issue in errors):
        return AllocationValidationResult(
            status="ALLOCATION_PARSE_ERROR",
            feasibility="uncertain",
            reason="Allocation JSON is structured, but dependency or action references cannot be interpreted reliably.",
            issues=issues,
        )

    if any(_is_infeasible_error(issue) for issue in errors):
        return AllocationValidationResult(
            status="ALLOCATION_INFEASIBLE",
            feasibility="infeasible",
            reason="At least one allocation issue cannot be repaired with the current robot skills and helper set.",
            issues=issues,
        )

    return AllocationValidationResult(
        status="REPAIRABLE_ALLOCATION_ERROR",
        feasibility="uncertain",
        reason="Allocation may be repairable, but it violates robot skill or object ownership constraints.",
        issues=issues,
    )


def _is_parse_error(issue):
    return issue.code in {
        "ALLOCATION_PARSE_ERROR",
        "DUPLICATE_STEP_ID",
        "UNKNOWN_DEPENDENCY",
        "DEPENDENCY_CYCLE",
        "UNKNOWN_ROBOT",
        "UNKNOWN_HELPER",
    }


def _is_infeasible_error(issue):
    if issue.code == "ROBOT_SKILL_MISMATCH" and not issue.details.get("candidate_robots"):
        return True
    if issue.code == "UNSUPPORTED_HANDOFF_REQUIRED":
        current_holder = issue.details.get("current_holder")
        action = issue.details.get("action")
        if current_holder and action:
            return False
    return False


def _add(issues, severity, code, message, path="", suggestion="", details=None):
    issues.append(
        AllocationIssue(
            severity=severity,
            code=code,
            message=message,
            path=path,
            suggestion=suggestion,
            details=details or {},
        )
    )
