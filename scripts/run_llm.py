import copy
import glob
import json
import ast
import os
import argparse
from pathlib import Path
from datetime import datetime
import random
import re
import subprocess
from typing import Literal, TypedDict

import openai
import ai2thor.controller

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:
    END = None
    START = None
    StateGraph = None

import sys
sys.path.append(".")

import resources.robots as robots
from validate_plan import (
    classify_validation_result,
    format_validation_report,
    validate_log_plan,
    write_validation_report,
)
from validate_allocation import (
    format_allocation_validation_report,
    validate_allocation_plan,
)
from compare_allocation_code import (
    compare_allocation_to_code,
    write_consistency_outputs,
)

IMPLEMENTED_AI2THOR_ACTIONS = [
    "GoToObject <robot><object>",
    "OpenObject <robot><object>",
    "CloseObject <robot><object>",
    "BreakObject <robot><object>",
    "SliceObject <robot><object>",
    "SwitchOn <robot><object>",
    "SwitchOff <robot><object>",
    "CleanObject <robot><object>",
    "PickupObject <robot><object>",
    "PutObject <robot><object><receptacleObject>",
    "ThrowObject <robot><object>",
]
IMPLEMENTED_ACTION_NAMES = {action.split()[0] for action in IMPLEMENTED_AI2THOR_ACTIONS}
IMPLEMENTED_AI2THOR_ACTIONS_TEXT = ", ".join(IMPLEMENTED_AI2THOR_ACTIONS)
CODE_GENERATION_SYSTEM_MESSAGE = (
    "You are a Robot Task Allocation Expert generating executable Python code for AI2-THOR helper functions. "
    "Return only executable Python code. Do not include Markdown, code fences, headings, explanations, or prose. "
    "You must use exactly the helper function signatures provided by the user. "
    "Every helper call must include a robot argument first. Do not invent arguments or transfer actions."
)
ALLOCATION_SYSTEM_MESSAGE = (
    "You are a Robot Task Allocation Expert. Determine whether subtasks must be performed sequentially, "
    "in parallel, or both. First identify dependencies caused by held objects or tools. "
    "There is no TransferObject helper in this codebase, so do not split a held object or tool across robots. "
    "If a dependent sequence requires the same held object or tool, assign the whole sequence to one capable robot when possible. "
    "Only form teams when the subtasks are independent, or when a team can execute without unsupported object handoff. "
    "In Task Allocation based on Robot Skills alone, first check if robot teams are required, then ensure robot skills or team skills match the required skills. "
    "In Task Allocation based on Mass alone, first check if robot teams are required, then ensure robot mass capacity or team mass capacity is sufficient. "
    "If multiple executable allocations exist, choose the best available option by reasoning to the best of your ability."
)
ALLOCATION_STRUCTURE_SYSTEM_MESSAGE = (
    "You convert robot task allocation reasoning into strict JSON. "
    "Return only valid JSON. Do not include Markdown, code fences, headings, explanations, feasibility assessments, reasons, or issues."
)
ALLOCATION_SEMANTIC_REVIEW_SYSTEM_MESSAGE = (
    "You review structured robot task allocations for semantic risks. "
    "Return only valid JSON using the requested schema. Do not include Markdown, headings, or prose. "
    "Your review is observational metadata, not a final routing decision."
)
ALLOWED_SEMANTIC_STATUSES = {"PASS", "SUSPICIOUS", "FAIL", "UNKNOWN"}
ALLOWED_SEMANTIC_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
ALLOWED_CONCERN_CODES = {
    "UNSUPPORTED_HANDOFF_RISK",
    "TASK_INTENT_MISMATCH",
    "MISSING_SUBTASK",
    "DEPENDENT_OBJECT_SEQUENCE_SPLIT",
    "PARALLELISM_CONFLICT",
    "UNCLEAR_ROBOT_ASSIGNMENT",
    "OBJECT_GOAL_AMBIGUOUS",
}


class SmartLLMState(TypedDict, total=False): # 클래스명 옆 ()는 상속할 부모 클래스인데 TypedDict는 일반적인 클래스상속으로 사용되지 않음
    floor_plan: int
    objects_ai: str
    decompose_prompt: str
    allocated_prompt: str
    code_prompt: str
    prompt_policy: str
    log_results: bool
    date_time: str
    log_path: str
    folder_name: str
    task: str
    task_index: int
    decomposed_plan: str
    allocated_plan: str
    allocation_plan: dict
    allocation_plan_raw: str
    allocation_structure_status: str
    allocation_structure_report: str
    allocation_structure_attempts: int
    allocation_validation_status: str
    allocation_validation_feasibility: str
    allocation_validation_result: dict
    allocation_validation_report: str
    semantic_allocation_review: dict
    semantic_allocation_review_raw: str
    semantic_allocation_review_status: str
    code_actions: list
    allocation_actions: list
    allocation_code_consistency_status: str
    allocation_code_consistency_report: dict
    code_plan: str
    task_robots: list
    ground_truth: list
    trans: int
    max_trans: int
    gpt_version: str
    repair_attempts: int
    attempt: int
    validation_status: str
    validation_report: str


def LM(prompt, gpt_version, max_tokens=128, temperature=0, stop=None, logprobs=1, frequency_penalty=0):
    
    if "gpt" not in gpt_version:
        response = openai.Completion.create(model=gpt_version, 
                                            prompt=prompt, 
                                            max_tokens=max_tokens, 
                                            temperature=temperature, 
                                            stop=stop, 
                                            logprobs=logprobs, 
                                            frequency_penalty = frequency_penalty)
        
        return response, response["choices"][0]["text"].strip()
    
    else:
        response = openai.ChatCompletion.create(model=gpt_version, 
                                            messages=prompt, 
                                            max_tokens=max_tokens, 
                                            temperature=temperature, 
                                            frequency_penalty = frequency_penalty)
        
        return response, response["choices"][0]["message"]["content"].strip()

def set_api_key(openai_api_key):
    openai.api_key = Path(openai_api_key + '.txt').read_text()

def extract_python_code(text):
    if "```" not in text:
        return text.strip()

    code_blocks = text.split("```")
    for block in code_blocks[1::2]: # 코드블럭에서 ```를 제외한 코드라인만 추출
        lines = block.splitlines()
        if lines and lines[0].strip().lower() in ("python", "py"):
            return "\n".join(lines[1:]).strip()

    return code_blocks[1].strip()


def strip_fenced_block(block):
    lines = block.strip().splitlines()
    if lines and lines[0].strip().lower() in ("json", "javascript", "js"):
        return "\n".join(lines[1:]).strip()
    return block.strip()


def parse_json_from_llm_output(text):
    candidates = []

    if "```" in text:
        code_blocks = text.split("```")
        candidates.extend(strip_fenced_block(block) for block in code_blocks[1::2])

    candidates.append(text.strip())

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and start < end:
        candidates.append(text[start:end + 1].strip())

    errors = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate), candidate
        except json.JSONDecodeError as exc:
            errors.append(str(exc))

    detail = errors[-1] if errors else "no JSON object found"
    raise ValueError(f"Could not parse allocation JSON: {detail}")


def find_disallowed_allocation_keys(value, path="$"):
    disallowed = {"feasibility", "reason", "issues"}
    found = []

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in disallowed:
                found.append(child_path)
            found.extend(find_disallowed_allocation_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_disallowed_allocation_keys(child, f"{path}[{index}]"))

    return found


def validate_allocation_plan_shape(allocation_plan, task_robots):
    issues = []
    robot_names = {robot["name"] for robot in task_robots}

    if not isinstance(allocation_plan, dict):
        return ["allocation_plan must be a JSON object."]

    top_level_keys = set(allocation_plan)
    if top_level_keys != {"assignments"}:
        issues.append("allocation_plan must contain exactly one top-level key: assignments.")

    disallowed_paths = find_disallowed_allocation_keys(allocation_plan)
    if disallowed_paths:
        issues.append(
            "allocation_plan must not include validator fields: "
            + ", ".join(disallowed_paths)
        )

    assignments = allocation_plan.get("assignments")
    if not isinstance(assignments, list):
        issues.append("allocation_plan.assignments must be a list.")
        return issues

    if not assignments:
        issues.append("allocation_plan.assignments must contain at least one assignment.")

    for assignment_index, assignment in enumerate(assignments):
        assignment_path = f"assignments[{assignment_index}]"
        if not isinstance(assignment, dict):
            issues.append(f"{assignment_path} must be an object.")
            continue

        step_id = assignment.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip():
            issues.append(f"{assignment_path}.step_id must be a non-empty string.")
        elif re.fullmatch(r"subtask_\d+", step_id) is None:
            issues.append(f"{assignment_path}.step_id must use the format subtask_1, subtask_2, ...")

        description = assignment.get("description")
        if not isinstance(description, str) or not description.strip():
            issues.append(f"{assignment_path}.description must be a non-empty string.")

        robot = assignment.get("robot")
        if robot not in robot_names:
            issues.append(f"{assignment_path}.robot must be one of {sorted(robot_names)}.")

        depends_on = assignment.get("depends_on")
        if not isinstance(depends_on, list):
            issues.append(f"{assignment_path}.depends_on must be a list.")

        actions = assignment.get("actions")
        if not isinstance(actions, list):
            issues.append(f"{assignment_path}.actions must be a list.")
            continue

        for action_index, action in enumerate(actions):
            action_path = f"{assignment_path}.actions[{action_index}]"
            if not isinstance(action, dict):
                issues.append(f"{action_path} must be an object.")
                continue

            helper = action.get("helper")
            if helper not in IMPLEMENTED_ACTION_NAMES:
                issues.append(f"{action_path}.helper must be one of {sorted(IMPLEMENTED_ACTION_NAMES)}.")

            action_robot = action.get("robot")
            if action_robot not in robot_names:
                issues.append(f"{action_path}.robot must be one of {sorted(robot_names)}.")

            obj = action.get("object")
            if not isinstance(obj, str) or not obj.strip():
                issues.append(f"{action_path}.object must be a non-empty string.")

            receptacle = action.get("receptacle")
            if helper == "PutObject":
                if not isinstance(receptacle, str) or not receptacle.strip():
                    issues.append(f"{action_path}.receptacle must be a non-empty string for PutObject.")
            elif receptacle is not None:
                issues.append(f"{action_path}.receptacle must be null unless helper is PutObject.")

    return issues

def build_repair_prompt(task, decomposed_plan, allocated_plan, current_code_plan, validation_report, task_robots, ground_truth):
    prompt = "from skills import " + IMPLEMENTED_AI2THOR_ACTIONS_TEXT
    prompt += "\nimport time"
    prompt += "\nimport threading"
    prompt += f"\n\n# Task Description: {task}"
    prompt += f"\n\n# Ground Truth Goal\n{ground_truth}"
    prompt += f"\n\n# Available Robots\nrobots = {task_robots}"
    prompt += "\n\n# GENERAL TASK DECOMPOSITION\n"
    prompt += decomposed_plan
    prompt += "\n\n# TASK ALLOCATION\n"
    prompt += allocated_plan
    prompt += "\n\n# INVALID CODE PLAN\n```python\n"
    prompt += current_code_plan
    prompt += "\n```"
    prompt += "\n\n# VALIDATION FEEDBACK\n"
    prompt += validation_report
    prompt += "\n\n# REPAIR CONSTRAINTS"
    prompt += "\n# Generate a corrected code plan for the same task."
    prompt += "\n# Prefer a valid executable plan over preserving an invalid robot assignment."
    prompt += "\n# Do not copy helper calls from the decomposition if they omit robot arguments."
    prompt += "\n# Every helper call must include a robot argument first."
    prompt += "\n# Correct examples: GoToObject(robot_list[0], 'Lettuce'), PickupObject(robot_list[0], 'Lettuce'), PutObject(robot_list[0], 'Lettuce', 'CounterTop')."
    prompt += "\n# Incorrect examples: GoToObject('Lettuce'), PickupObject('Lettuce'), PutObject('Lettuce', 'CounterTop')."
    prompt += "\n# Define task functions with robot_list or robot parameters, then call them with robots[index] or [robots[index], ...]."
    prompt += "\n# Use the minimum number of robots necessary."
    prompt += "\n# If a subtask sequence depends on the same held object or tool and there is no TransferObject helper, consolidate that sequence onto one capable robot."
    prompt += "\n# Keep object manipulation on the robot currently holding the object."
    prompt += "\n# Do not split PickupObject and PutObject/ThrowObject across robots unless TransferObject exists."
    prompt += "\n# TransferObject does not exist in this codebase."
    prompt += "\n# If validation says a PickupObject is unnecessary for the final goal, remove that PickupObject instead of putting down the currently held object."
    prompt += "\n# If the goal says a receptacle contains an object, prefer PutObject(robot, object, receptacleObject) over ThrowObject."
    prompt += "\n# Use only implemented helper signatures exactly as provided."
    prompt += "\n# Return only corrected executable Python code."
    prompt += "\n\n# CORRECTED CODE PLAN\n"
    return prompt

def build_decompose_prompt(objects_ai, decompose_prompt):
    prompt = f"from skills import " + IMPLEMENTED_AI2THOR_ACTIONS_TEXT
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    prompt += objects_ai
    prompt += "\n\n" + decompose_prompt
    return prompt


def build_allocation_prompt(allocated_prompt):
    prompt = f"from skills import " + IMPLEMENTED_AI2THOR_ACTIONS_TEXT
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    prompt += "\n\n" + allocated_prompt + "\n\n"
    return prompt


def build_code_prompt(objects_ai, code_prompt):
    prompt = f"from skills import " + IMPLEMENTED_AI2THOR_ACTIONS_TEXT
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    prompt += objects_ai
    prompt += "\n\n" + code_prompt + "\n\n"
    return prompt


def build_allocation_structure_prompt(state, previous_error=None):
    prompt = "# Task Description\n"
    prompt += state["task"]
    prompt += "\n\n# GENERAL TASK DECOMPOSITION\n"
    prompt += state["decomposed_plan"]
    prompt += "\n\n# NATURAL LANGUAGE TASK ALLOCATION\n"
    prompt += state["allocated_plan"]
    prompt += "\n\n# Available Robots\n"
    prompt += f"robots = {state['task_robots']}"
    prompt += "\n\n# Available Objects\n"
    prompt += state["objects_ai"]
    prompt += "\n\n# JSON OUTPUT REQUIREMENTS\n"
    prompt += "\nReturn only one valid JSON object. Do not include Markdown or code fences."
    prompt += "\nThe JSON must contain exactly one top-level key: assignments."
    prompt += "\nDo not include feasibility, reason, issues, semantic_status, confidence, or concerns."
    prompt += "\nUse runtime robot names exactly as provided, such as robot1 or robot2."
    prompt += "\nUse helper names only from this set: " + ", ".join(sorted(IMPLEMENTED_ACTION_NAMES)) + "."
    prompt += "\nUse step_id values in this format: subtask_1, subtask_2, ..."
    prompt += "\nFor PutObject actions, receptacle must be the target receptacle object string."
    prompt += "\nFor every non-PutObject action, receptacle must be null."
    prompt += "\nUse depends_on as a list of step_id strings."
    prompt += "\n\n# Required JSON shape\n"
    prompt += json.dumps(
        {
            "assignments": [
                {
                    "step_id": "subtask_1",
                    "description": "short subtask description",
                    "robot": "robot1",
                    "actions": [
                        {
                            "helper": "PickupObject",
                            "robot": "robot1",
                            "object": "Mug",
                            "receptacle": None,
                        }
                    ],
                    "depends_on": [],
                }
            ]
        },
        indent=2,
    )

    if previous_error:
        prompt += "\n\n# Previous invalid output problem\n"
        prompt += previous_error
        prompt += "\nReturn corrected JSON only."

    return prompt


def build_allocation_semantic_review_prompt(state):
    prompt = "# Task Description\n"
    prompt += state["task"]
    prompt += "\n\n# Ground Truth Goal\n"
    prompt += str(state["ground_truth"])
    prompt += "\n\n# GENERAL TASK DECOMPOSITION\n"
    prompt += state["decomposed_plan"]
    prompt += "\n\n# NATURAL LANGUAGE TASK ALLOCATION\n"
    prompt += state["allocated_plan"]
    prompt += "\n\n# STRUCTURED ALLOCATION JSON\n"
    prompt += json.dumps(state["allocation_plan"], indent=2)
    prompt += "\n\n# DETERMINISTIC ALLOCATION VALIDATION\n"
    prompt += state.get("allocation_validation_report", "Allocation validation has not run.")
    prompt += "\n\n# Available Robots\n"
    prompt += f"robots = {state['task_robots']}"
    prompt += "\n\n# Review Requirements"
    prompt += "\nReturn only one valid JSON object with exactly these keys:"
    prompt += "\nsemantic_status, concern_codes, confidence, concerns."
    prompt += "\nsemantic_status must be one of: PASS, SUSPICIOUS, FAIL, UNKNOWN."
    prompt += "\nconfidence must be one of: LOW, MEDIUM, HIGH."
    prompt += "\nconcern_codes must use only: " + ", ".join(sorted(ALLOWED_CONCERN_CODES)) + "."
    prompt += "\nconcerns must be a list of short human-readable strings."
    prompt += "\nDo not include routing decisions or code generation instructions."
    prompt += "\n\n# Required JSON shape\n"
    prompt += json.dumps(
        {
            "semantic_status": "PASS",
            "concern_codes": [],
            "confidence": "LOW",
            "concerns": [],
        },
        indent=2,
    )
    return prompt


def normalize_semantic_review(review):
    if not isinstance(review, dict):
        raise ValueError("semantic review must be a JSON object.")

    semantic_status = review.get("semantic_status")
    confidence = review.get("confidence")
    concern_codes = review.get("concern_codes")
    concerns = review.get("concerns")

    if semantic_status not in ALLOWED_SEMANTIC_STATUSES:
        raise ValueError("semantic_status must be one of " + ", ".join(sorted(ALLOWED_SEMANTIC_STATUSES)) + ".")
    if confidence not in ALLOWED_SEMANTIC_CONFIDENCE:
        raise ValueError("confidence must be one of " + ", ".join(sorted(ALLOWED_SEMANTIC_CONFIDENCE)) + ".")
    if not isinstance(concern_codes, list):
        raise ValueError("concern_codes must be a list.")
    invalid_codes = [code for code in concern_codes if code not in ALLOWED_CONCERN_CODES]
    if invalid_codes:
        raise ValueError("concern_codes contains unsupported values: " + ", ".join(map(str, invalid_codes)) + ".")
    if not isinstance(concerns, list) or not all(isinstance(concern, str) for concern in concerns):
        raise ValueError("concerns must be a list of strings.")

    return {
        "semantic_status": semantic_status,
        "concern_codes": concern_codes,
        "confidence": confidence,
        "concerns": concerns,
    }


def unknown_semantic_review(reason, raw=""):
    return {
        "semantic_status": "UNKNOWN",
        "concern_codes": [],
        "confidence": "LOW",
        "concerns": [reason],
        "raw": raw,
    }


def make_task_folder_name(task, date_time):
    task_name = "{fxn}".format(fxn='_'.join(task.split(' ')))
    task_name = task_name.replace('\n','')
    return f"{task_name}_plans_{date_time}"


def write_task_log_header(log_path, state):
    with open(f"{log_path}/log.txt", 'w') as f:
        f.write(state["task"])
        f.write(f"\n\nGPT Version: {state['gpt_version']}")
        f.write(f"\n\nFloor Plan: {state['floor_plan']}")
        f.write(f"\n{state['objects_ai']}")
        f.write(f"\nrobots = {state['task_robots']}")
        f.write(f"\nground_truth = {state['ground_truth']}")
        f.write(f"\ntrans = {state['trans']}")
        f.write(f"\nmax_trans = {state['max_trans']}")
        f.write(f"\nPrompt Policy: {state.get('prompt_policy', 'execution-aware')}")


def write_stage4_outputs(log_path, state):
    validation_result = state.get("allocation_validation_result")
    if validation_result is not None:
        with open(f"{log_path}/allocation_validation_result.json", 'w') as validation_file:
            json.dump(validation_result, validation_file, indent=2)
            validation_file.write("\n")

    validation_report = state.get("allocation_validation_report")
    if validation_report is not None:
        with open(f"{log_path}/allocation_validation_report.txt", 'w') as validation_report_file:
            validation_report_file.write(validation_report)
            validation_report_file.write("\n")

    semantic_review = state.get("semantic_allocation_review")
    if semantic_review is not None:
        with open(f"{log_path}/semantic_allocation_review.json", 'w') as semantic_file:
            json.dump(semantic_review, semantic_file, indent=2)
            semantic_file.write("\n")


def code_generation_constraints(prompt_policy):
    constraints = [
        "# Use only these implemented helper signatures: GoToObject(robot, object), PickupObject(robot, object), PutObject(robot, object, receptacleObject), ThrowObject(robot, object), SliceObject(robot, object), CleanObject(robot, object), OpenObject(robot, object), CloseObject(robot, object), SwitchOn(robot, object), SwitchOff(robot, object), BreakObject(robot, object).",
        "# Do not use DropHandObject, PushObject, or PullObject because they are not implemented helper functions in data/aithor_connect/aithor_connect.py.",
        "# Never add a target receptacle argument to ThrowObject. Correct: ThrowObject(robot, object). Incorrect: ThrowObject(robot, object, receptacle).",
        "# Preserve object ownership: if a robot picks up an object, only that same robot may later PutObject or ThrowObject that held object. There is no TransferObject helper.",
        "# Do not add PickupObject unless the object must be moved, placed into a receptacle, or explicitly held for the task.",
    ]

    if prompt_policy == "strict-allocation":
        return [
            "# Follow the TASK ALLOCATION as closely as possible, even when this exposes an execution constraint problem.",
            "# Do not silently consolidate, reassign, or bypass robot assignments from TASK ALLOCATION.",
        ] + constraints

    return [
        "# Follow the TASK ALLOCATION when it is directly executable under the helper and object ownership constraints.",
        "# If the allocation splits a dependent sequence across robots and there is no TransferObject helper, consolidate that sequence onto one capable robot.",
    ] + constraints


def code_generation_system_message(prompt_policy):
    if prompt_policy == "strict-allocation":
        return CODE_GENERATION_SYSTEM_MESSAGE + " Strictly preserve the robot assignment from TASK ALLOCATION."

    return CODE_GENERATION_SYSTEM_MESSAGE + " Strictly preserve the robot assignment from TASK ALLOCATION unless it is impossible under the provided constraints."


def decompose_task_node(state: SmartLLMState):
    print(f"Generating Decomposed Plan for task {state['task_index'] + 1}: {state['task']}")
    prompt = build_decompose_prompt(state["objects_ai"], state["decompose_prompt"])
    curr_prompt = f"{prompt}\n\n# Task Description: {state['task']}"

    if "gpt" not in state["gpt_version"]:
        _, text = LM(curr_prompt, state["gpt_version"], max_tokens=1000, stop=["def"], frequency_penalty=0.15)
    else:
        messages = [{"role": "user", "content": curr_prompt}]
        _, text = LM(messages, state["gpt_version"], max_tokens=1300, frequency_penalty=0.0)

    return {"decomposed_plan": text}


def allocate_task_node(state: SmartLLMState):
    print(f"Generating Allocation Solution for task {state['task_index'] + 1}: {state['task']}")
    prompt = build_allocation_prompt(state["allocated_prompt"])
    no_robot = len(state["task_robots"])
    curr_prompt = prompt + state["decomposed_plan"]
    curr_prompt += f"\n# TASK ALLOCATION"
    curr_prompt += f"\n# Scenario: There are {no_robot} robots available, The task should be performed using the minimum number of robots necessary. Robots should be assigned to subtasks that match its skills and mass capacity. Using your reasoning come up with a solution to satisfy all contraints."
    curr_prompt += f"\n\nrobots = {state['task_robots']}"
    curr_prompt += f"\n{state['objects_ai']}"
    curr_prompt += f"\n\n# IMPORTANT: The AI should ensure that the robots assigned to the tasks have all the necessary skills to perform the tasks. IMPORTANT: Determine whether the subtasks must be performed sequentially or in parallel, or a combination of both and allocate robots based on availablitiy. "
    curr_prompt += "\n# EXECUTION-AWARE ALLOCATION CONSTRAINTS:"
    curr_prompt += "\n# There is no TransferObject helper in this codebase."
    curr_prompt += "\n# Do not form a robot team only by taking the union of skills if execution would require one robot to hand a held object or tool to another robot."
    curr_prompt += "\n# If subtasks form a dependent sequence through the same held object or tool, assign the whole dependent sequence to one capable robot whenever such a robot exists."
    curr_prompt += "\n# Use multiple robots for independent subtasks or parallel work that does not require unsupported object handoff."
    curr_prompt += "\n# If no allocation can execute without unsupported object handoff, state that the allocation is infeasible under the current helper set."
    curr_prompt += f"\n# SOLUTION  \n"

    if "gpt" not in state["gpt_version"]:
        _, text = LM(curr_prompt, state["gpt_version"], max_tokens=1000, stop=["def"], frequency_penalty=0.65)
    elif "gpt-3.5" in state["gpt_version"]:
        messages = [{"role": "user", "content": curr_prompt}]
        _, text = LM(messages, state["gpt_version"], max_tokens=1500, frequency_penalty=0.35)
    else:
        messages = [
            {"role": "system", "content": ALLOCATION_SYSTEM_MESSAGE},
            {"role": "system", "content": "You are a Robot Task Allocation Expert"},
            {"role": "user", "content": curr_prompt},
        ]
        _, text = LM(messages, state["gpt_version"], max_tokens=400, frequency_penalty=0.69)

    return {"allocated_plan": text}


def structure_allocation_node(state: SmartLLMState):
    print(f"Structuring Allocation Plan for task {state['task_index'] + 1}: {state['task']}")
    attempts = max(1, state.get("allocation_structure_attempts", 2))
    previous_error = None
    last_raw = ""
    last_report = ""

    for attempt in range(1, attempts + 1):
        prompt = build_allocation_structure_prompt(state, previous_error)

        if "gpt" not in state["gpt_version"]:
            _, text = LM(
                prompt,
                state["gpt_version"],
                max_tokens=1000,
                stop=None,
                frequency_penalty=0.0,
            )
        else:
            messages = [
                {"role": "system", "content": ALLOCATION_STRUCTURE_SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ]
            _, text = LM(
                messages,
                state["gpt_version"],
                max_tokens=1200,
                frequency_penalty=0.0,
            )

        last_raw = text

        try:
            allocation_plan, parsed_json = parse_json_from_llm_output(text)
            issues = validate_allocation_plan_shape(allocation_plan, state["task_robots"])
            if issues:
                raise ValueError("\n".join(f"- {issue}" for issue in issues))

            report = f"STRUCTURE_ALLOCATION_PASS after {attempt} attempt(s)."
            return {
                "allocation_plan": allocation_plan,
                "allocation_plan_raw": parsed_json,
                "allocation_structure_status": "STRUCTURE_ALLOCATION_PASS",
                "allocation_structure_report": report,
            }
        except ValueError as exc:
            last_report = f"STRUCTURE_ALLOCATION_ERROR on attempt {attempt}/{attempts}:\n{exc}"
            previous_error = last_report

    print(last_report)
    return {
        "allocation_plan_raw": last_raw,
        "allocation_structure_status": "STRUCTURE_ALLOCATION_ERROR",
        "allocation_structure_report": last_report,
        "validation_status": "STRUCTURE_ALLOCATION_ERROR",
        "validation_report": last_report,
    }


def validate_allocation_node(state: SmartLLMState):
    print(f"Validating Allocation Plan for task {state['task_index'] + 1}: {state['task']}")
    result = validate_allocation_plan(state["allocation_plan"], state["task_robots"])
    report = format_allocation_validation_report(result)
    print(report)

    return {
        "allocation_validation_status": result.status,
        "allocation_validation_feasibility": result.feasibility,
        "allocation_validation_result": result.to_dict(),
        "allocation_validation_report": report,
    }


def review_allocation_semantics_node(state: SmartLLMState):
    print(f"Reviewing Allocation Semantics for task {state['task_index'] + 1}: {state['task']}")
    prompt = build_allocation_semantic_review_prompt(state)

    if "gpt" not in state["gpt_version"]:
        _, text = LM(
            prompt,
            state["gpt_version"],
            max_tokens=800,
            stop=None,
            frequency_penalty=0.0,
        )
    else:
        messages = [
            {"role": "system", "content": ALLOCATION_SEMANTIC_REVIEW_SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ]
        _, text = LM(
            messages,
            state["gpt_version"],
            max_tokens=900,
            frequency_penalty=0.0,
        )

    try:
        parsed_review, parsed_json = parse_json_from_llm_output(text)
        review = normalize_semantic_review(parsed_review)
        raw = parsed_json
    except ValueError as exc:
        review = unknown_semantic_review(f"Semantic allocation review could not be parsed: {exc}", text)
        raw = text

    return {
        "semantic_allocation_review": review,
        "semantic_allocation_review_raw": raw,
        "semantic_allocation_review_status": review["semantic_status"],
    }


def generate_code_plan_node(state: SmartLLMState):
    print(f"Generating Allocated Code for task {state['task_index'] + 1}: {state['task']}")
    prompt = build_code_prompt(state["objects_ai"], state["code_prompt"])
    curr_prompt = prompt + state["decomposed_plan"]
    curr_prompt += f"\n# TASK ALLOCATION"
    curr_prompt += f"\n\nrobots = {state['task_robots']}"
    curr_prompt += state["allocated_plan"]
    curr_prompt += "\n\n# CODE GENERATION CONSTRAINTS"
    for constraint in code_generation_constraints(state.get("prompt_policy", "execution-aware")):
        curr_prompt += "\n" + constraint
    curr_prompt += f"\n# CODE Solution  \n"

    if "gpt" not in state["gpt_version"]:
        _, text = LM(curr_prompt, state["gpt_version"], max_tokens=1000, stop=["def"], frequency_penalty=0.30)
    else:
        messages = [
            {
                "role": "system",
                "content": code_generation_system_message(state.get("prompt_policy", "execution-aware")),
            },
            {"role": "user", "content": curr_prompt},
        ]
        _, text = LM(messages, state["gpt_version"], max_tokens=1400, frequency_penalty=0.4)

    return {"code_plan": extract_python_code(text)}


def save_plan_node(state: SmartLLMState):
    if not state.get("log_results", True):
        return {
            "validation_status": "SKIPPED",
            "validation_report": "Validation skipped because log_results is disabled.",
        }

    folder_name = make_task_folder_name(state["task"], state["date_time"])
    log_path = f"./logs/{folder_name}"
    os.mkdir(log_path)

    write_task_log_header(log_path, state)

    with open(f"{log_path}/decomposed_plan.py", 'w') as d:
        d.write(state["decomposed_plan"])

    with open(f"{log_path}/allocated_plan.py", 'w') as a:
        a.write(state["allocated_plan"])

    with open(f"{log_path}/allocation_plan.json", 'w') as allocation_file:
        json.dump(state["allocation_plan"], allocation_file, indent=2)
        allocation_file.write("\n")

    write_stage4_outputs(log_path, state)

    with open(f"{log_path}/code_plan.py", 'w') as x:
        x.write(state["code_plan"])

    return {
        "folder_name": folder_name,
        "log_path": log_path,
    }


def save_allocation_validation_failure_node(state: SmartLLMState):
    if not state.get("log_results", True):
        return {
            "validation_status": state.get("allocation_validation_status", "ALLOCATION_VALIDATION_ERROR"),
            "validation_report": state.get("allocation_validation_report", "Allocation validation failed."),
        }

    folder_name = make_task_folder_name(state["task"], state["date_time"])
    log_path = f"./logs/{folder_name}"
    os.mkdir(log_path)

    write_task_log_header(log_path, state)

    with open(f"{log_path}/decomposed_plan.py", 'w') as d:
        d.write(state["decomposed_plan"])

    with open(f"{log_path}/allocated_plan.py", 'w') as a:
        a.write(state["allocated_plan"])

    with open(f"{log_path}/allocation_plan.json", 'w') as allocation_file:
        json.dump(state["allocation_plan"], allocation_file, indent=2)
        allocation_file.write("\n")

    write_stage4_outputs(log_path, state)

    return {
        "folder_name": folder_name,
        "log_path": log_path,
        "validation_status": state.get("allocation_validation_status", "ALLOCATION_VALIDATION_ERROR"),
        "validation_report": state.get("allocation_validation_report", "Allocation validation failed."),
    }


def save_allocation_failure_node(state: SmartLLMState):
    if not state.get("log_results", True):
        return {
            "validation_status": "STRUCTURE_ALLOCATION_ERROR",
            "validation_report": state.get("allocation_structure_report", "Allocation structure failed."),
        }

    folder_name = make_task_folder_name(state["task"], state["date_time"])
    log_path = f"./logs/{folder_name}"
    os.mkdir(log_path)

    write_task_log_header(log_path, state)

    with open(f"{log_path}/decomposed_plan.py", 'w') as d:
        d.write(state["decomposed_plan"])

    with open(f"{log_path}/allocated_plan.py", 'w') as a:
        a.write(state["allocated_plan"])

    with open(f"{log_path}/allocation_structure_report.txt", 'w') as report:
        report.write(state.get("allocation_structure_report", "Allocation structure failed."))

    with open(f"{log_path}/allocation_plan_raw.txt", 'w') as raw:
        raw.write(state.get("allocation_plan_raw", ""))

    return {
        "folder_name": folder_name,
        "log_path": log_path,
        "validation_status": "STRUCTURE_ALLOCATION_ERROR",
        "validation_report": state.get("allocation_structure_report", "Allocation structure failed."),
    }


def validate_code_plan_node(state: SmartLLMState): # state는 LangGraph가 넘겨주는 현재 상태 dict
    if not state.get("log_results", True):
        return {}

    Path(state["log_path"], "code_plan.py").write_text(state["code_plan"])
    issues = validate_log_plan(state["log_path"])
    classification = classify_validation_result(state["log_path"], issues)
    write_validation_report(state["log_path"], issues, classification)
    validation_report = format_validation_report(issues, classification)
    print(validation_report)

    return {
        "validation_status": classification.status,
        "validation_report": validation_report,
    }


def check_allocation_code_consistency_node(state: SmartLLMState):
    result = compare_allocation_to_code(
        state["allocation_plan"],
        state["code_plan"],
        state["task_robots"],
        state.get("validation_status", "UNKNOWN"),
    )
    report = result.to_dict()
    print(f"Allocation-code consistency status: {result.status}")
    print(result.summary)

    if state.get("log_results", True) and state.get("log_path"):
        write_consistency_outputs(state["log_path"], result)

    return {
        "code_actions": result.code_actions,
        "allocation_actions": result.allocation_actions,
        "allocation_code_consistency_status": result.status,
        "allocation_code_consistency_report": report,
    }


def repair_code_plan_node(state: SmartLLMState):
    next_attempt = state.get("attempt", 0) + 1
    print(f"Regenerating code plan from validation feedback... attempt {next_attempt}/{state['repair_attempts']}")
    repair_prompt = build_repair_prompt(
        state["task"],
        state["decomposed_plan"],
        state["allocated_plan"],
        state["code_plan"],
        state["validation_report"],
        state["task_robots"],
        state["ground_truth"],
    )

    if "gpt" not in state["gpt_version"]:
        _, repaired_text = LM(
            repair_prompt,
            state["gpt_version"],
            max_tokens=1000,
            stop=["def"],
            frequency_penalty=0.30,
        )
    else:
        messages = [
            {"role": "system", "content": CODE_GENERATION_SYSTEM_MESSAGE},
            {"role": "user", "content": repair_prompt},
        ]
        _, repaired_text = LM(
            messages,
            state["gpt_version"],
            max_tokens=1400,
            frequency_penalty=0.2,
        )

    return {
        "code_plan": extract_python_code(repaired_text),
        "attempt": next_attempt,
    }


def route_after_validation(state: SmartLLMState) -> Literal["repair", "finish"]:
    """validation의 결과는 finish or repair """
    if state["validation_status"] != "REPAIRABLE_PLAN_ERROR":
        return "finish"

    if state.get("attempt", 0) >= state["repair_attempts"]:
        return "finish"

    return "repair"


def route_after_allocation_structure(state: SmartLLMState) -> Literal["validate", "fail"]:
    if state["allocation_structure_status"] == "STRUCTURE_ALLOCATION_PASS":
        return "validate"
    return "fail"


def route_after_allocation_semantic_review(state: SmartLLMState) -> Literal["generate", "fail"]:
    if (
        state.get("prompt_policy") == "strict-allocation"
        and state.get("allocation_validation_status") != "ALLOCATION_PASS"
    ):
        return "fail"
    return "generate"


def build_task_graph():
    if StateGraph is None:
        raise RuntimeError(
            "LangGraph is required for run_llm.py. Install dependencies with `pip install -r requirments.txt`."
        )

    builder = StateGraph(SmartLLMState) # 상태기반으로 전이할 수 있는 그래프 생성
    builder.add_node("decompose", decompose_task_node)
    builder.add_node("allocate", allocate_task_node)
    builder.add_node("structure_allocation", structure_allocation_node)
    builder.add_node("validate_allocation", validate_allocation_node)
    builder.add_node("review_allocation_semantics", review_allocation_semantics_node)
    builder.add_node("generate_code", generate_code_plan_node)
    builder.add_node("save_plan", save_plan_node)
    builder.add_node("save_allocation_failure", save_allocation_failure_node)
    builder.add_node("save_allocation_validation_failure", save_allocation_validation_failure_node)
    builder.add_node("validate_code", validate_code_plan_node)
    builder.add_node("check_allocation_code_consistency", check_allocation_code_consistency_node)
    builder.add_node("repair_code", repair_code_plan_node)
    builder.add_edge(START, "decompose")
    builder.add_edge("decompose", "allocate")
    builder.add_edge("allocate", "structure_allocation")
    builder.add_conditional_edges(
        "structure_allocation",
        route_after_allocation_structure,
        {
            "validate": "validate_allocation",
            "fail": "save_allocation_failure",
        },
    )
    builder.add_edge("validate_allocation", "review_allocation_semantics")
    builder.add_conditional_edges(
        "review_allocation_semantics",
        route_after_allocation_semantic_review,
        {
            "generate": "generate_code",
            "fail": "save_allocation_validation_failure",
        },
    )
    builder.add_edge("generate_code", "save_plan")
    builder.add_edge("save_plan", "validate_code")
    builder.add_edge("save_allocation_failure", END)
    builder.add_edge("save_allocation_validation_failure", END)
    builder.add_edge("validate_code", "check_allocation_code_consistency")
    builder.add_conditional_edges(
        "check_allocation_code_consistency",
        route_after_validation,
        {
            "repair": "repair_code",
            "finish": END,
        },
    )
    builder.add_edge("repair_code", "validate_code")
    return builder.compile()


def build_repair_graph():
    if StateGraph is None:
        raise RuntimeError(
            "LangGraph is required for run_llm.py. Install dependencies with `pip install -r requirments.txt`."
        )

    builder = StateGraph(SmartLLMState)
    builder.add_node("validate_code", validate_code_plan_node)
    builder.add_node("repair_code", repair_code_plan_node)
    builder.add_edge(START, "validate_code")
    builder.add_conditional_edges(
        "validate_code",
        route_after_validation,
        {
            "repair": "repair_code",
            "finish": END,
        },
    )
    builder.add_edge("repair_code", "validate_code")
    return builder.compile()


def repair_code_plan_if_needed(log_path, task, decomposed_plan, allocated_plan, code_plan, task_robots, ground_truth, gpt_version, repair_attempts):
    repair_graph = build_repair_graph()
    result = repair_graph.invoke(
        {
            "log_path": log_path,
            "log_results": True,
            "task": task,
            "decomposed_plan": decomposed_plan,
            "allocated_plan": allocated_plan,
            "code_plan": code_plan,
            "task_robots": task_robots,
            "ground_truth": ground_truth,
            "gpt_version": gpt_version,
            "repair_attempts": repair_attempts,
            "attempt": 0,
        },
        config={"recursion_limit": max(25, repair_attempts * 3 + 10)},
    )
    return result["code_plan"], result.get("validation_status", "UNKNOWN")

# Function returns object list with name and properties.
def convert_to_dict_objprop(objs, obj_mass):
    objs_dict = []
    for i, obj in enumerate(objs):
        obj_dict = {'name': obj , 'mass' : obj_mass[i]}
        # obj_dict = {'name': obj , 'mass' : 1.0}
        objs_dict.append(obj_dict)
    return objs_dict

def get_ai2_thor_objects(floor_plan_id):
    # connector to ai2thor to get object list
    controller = ai2thor.controller.Controller(scene="FloorPlan"+str(floor_plan_id))
    obj = list([obj["objectType"] for obj in controller.last_event.metadata["objects"]])
    obj_mass = list([obj["mass"] for obj in controller.last_event.metadata["objects"]])
    controller.stop()
    obj = convert_to_dict_objprop(obj, obj_mass)
    return obj

def parse_task_record(line, line_number, source_path):
    line = line.strip()
    if not line:
        return None

    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        try:
            record = ast.literal_eval(line)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"Could not parse {source_path}:{line_number}: {exc}") from exc

    return {
        "task": record["task"],
        "robot list": record["robot list"],
        "object_states": record["object_states"],
        "trans": record.get("trans", 0),
        "max_trans": record.get("max_trans", 0),
    }

if __name__ == "__main__": # 파일 직접 실행시에만 코드 돌리기, import시에는 돌리지마라
    parser = argparse.ArgumentParser()
    parser.add_argument("--floor-plan", type=int, required=True) # 커맨드라인 옵션 등록
    parser.add_argument("--openai-api-key-file", type=str, default="api_key")
    parser.add_argument("--gpt-version", type=str, default="gpt-4o-mini",
                        choices=['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-3.5-turbo', 'gpt-4', 'gpt-3.5-turbo-16k'])
    
    parser.add_argument("--prompt-decompse-set", type=str, default="train_task_decompose", 
                        choices=['train_task_decompose'])
    
    parser.add_argument("--prompt-allocation-set", type=str, default="train_task_allocation", 
                        choices=['train_task_allocation'])
    
    parser.add_argument("--test-set", type=str, default="final_test", 
                        choices=['final_test'])
    
    parser.add_argument("--prompt-policy", type=str, default="execution-aware",
                        choices=["execution-aware", "strict-allocation"])
    parser.add_argument("--log-results", type=bool, default=True)
    parser.add_argument("--repair-attempts", type=int, default=3)
    parser.add_argument("--allocation-structure-attempts", type=int, default=2)
    
    args = parser.parse_args() # 커맨드라인 옵션으로 등록한 매개변수 args에 저장

    set_api_key(args.openai_api_key_file)
    
    if not os.path.isdir(f"./logs/"): # logs 폴더가 현재 실행 위치 기준으로 존재하는지 확인
        os.makedirs(f"./logs/")
        
    # read the tasks        
    test_tasks = []
    robots_test_tasks = []  
    gt_test_tasks = []    
    trans_cnt_tasks = []
    max_trans_cnt_tasks = []  
    task_file = f"./data/{args.test_set}/FloorPlan{args.floor_plan}.json"
    with open(task_file, "r") as f:
        for line_number, line in enumerate(f.readlines(), 1):
            record = parse_task_record(line, line_number, task_file)
            if record is None:
                continue
            test_tasks.append(record["task"])
            robots_test_tasks.append(record["robot list"])
            gt_test_tasks.append(record["object_states"])
            trans_cnt_tasks.append(record["trans"])
            max_trans_cnt_tasks.append(record["max_trans"])
                    
    print(f"\n----Test set tasks----\n{test_tasks}\nTotal: {len(test_tasks)} tasks\n")
    # prepare list of robots for the tasks
    available_robots = []
    for robots_list in robots_test_tasks:
        task_robots = []
        for i, r_id in enumerate(robots_list):
            rob = copy.deepcopy(robots.robots[r_id-1])
            rob['skills'] = [skill for skill in rob['skills'] if skill in IMPLEMENTED_ACTION_NAMES]
            # rename the robot
            rob['name'] = 'robot' + str(i+1)
            task_robots.append(rob)
        available_robots.append(task_robots)
        
    objects_ai = f"\n\nobjects = {get_ai2_thor_objects(args.floor_plan)}"
    
    # read input train prompts
    decompose_prompt_file = open(os.getcwd() + "/data/pythonic_plans/" + args.prompt_decompse_set + ".py", "r")
    decompose_prompt = decompose_prompt_file.read()
    decompose_prompt_file.close()
    
    prompt_file = os.getcwd() + "/data/pythonic_plans/" + args.prompt_allocation_set + "_solution.py"
    allocated_prompt_file = open(prompt_file, "r")
    allocated_prompt = allocated_prompt_file.read()
    allocated_prompt_file.close()

    prompt_file1 = os.getcwd() + "/data/pythonic_plans/" + args.prompt_allocation_set + "_code.py"
    code_prompt_file = open(prompt_file1, "r")
    code_prompt = code_prompt_file.read()
    code_prompt_file.close()

    now = datetime.now() # current date and time
    date_time = now.strftime("%m-%d-%Y-%H-%M-%S")
    task_graph = build_task_graph()

    for idx, task in enumerate(test_tasks):
        result = task_graph.invoke(
            {
                "floor_plan": args.floor_plan,
                "objects_ai": objects_ai,
                "decompose_prompt": decompose_prompt,
                "allocated_prompt": allocated_prompt,
                "code_prompt": code_prompt,
                "prompt_policy": args.prompt_policy,
                "log_results": args.log_results,
                "date_time": date_time,
                "task": task,
                "task_index": idx,
                "task_robots": available_robots[idx],
                "ground_truth": gt_test_tasks[idx],
                "trans": trans_cnt_tasks[idx],
                "max_trans": max_trans_cnt_tasks[idx],
                "gpt_version": args.gpt_version,
                "repair_attempts": args.repair_attempts,
                "allocation_structure_attempts": args.allocation_structure_attempts,
                "attempt": 0,
            },
            config={"recursion_limit": max(25, args.repair_attempts * 3 + 10)},
        )
        print(f"Validation status for {result.get('folder_name', task)}: {result.get('validation_status', 'UNKNOWN')}")
            
