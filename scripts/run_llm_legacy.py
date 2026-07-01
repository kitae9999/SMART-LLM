import copy
import glob
import json
import ast
import os
import argparse
from pathlib import Path
from datetime import datetime
import random
import subprocess

import openai
import ai2thor.controller

import sys
sys.path.append(".")

import resources.robots as robots
from validate_plan import (
    classify_validation_result,
    format_validation_report,
    validate_log_plan,
    write_validation_report,
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

def repair_code_plan_if_needed(log_path, task, decomposed_plan, allocated_plan, code_plan, task_robots, ground_truth, gpt_version, repair_attempts):
    current_code_plan = code_plan
    for attempt in range(repair_attempts + 1):
        issues = validate_log_plan(log_path)
        classification = classify_validation_result(log_path, issues)
        write_validation_report(log_path, issues, classification)
        print(format_validation_report(issues, classification))

        if classification.status != "REPAIRABLE_PLAN_ERROR" or attempt == repair_attempts:
            return current_code_plan, classification.status

        print(f"Regenerating code plan from validation feedback... attempt {attempt + 1}/{repair_attempts}")
        validation_report = format_validation_report(issues, classification)
        repair_prompt = build_repair_prompt(
            task,
            decomposed_plan,
            allocated_plan,
            current_code_plan,
            validation_report,
            task_robots,
            ground_truth,
        )

        if "gpt" not in gpt_version:
            _, repaired_text = LM(repair_prompt, gpt_version, max_tokens=1000, stop=["def"], frequency_penalty=0.30)
        else:
            messages = [
                {"role": "system", "content": CODE_GENERATION_SYSTEM_MESSAGE},
                {"role": "user", "content": repair_prompt},
            ]
            _, repaired_text = LM(messages, gpt_version, max_tokens=1400, frequency_penalty=0.2)

        current_code_plan = extract_python_code(repaired_text)
        Path(log_path, "code_plan.py").write_text(current_code_plan)

    return current_code_plan, "UNKNOWN"

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
    
    parser.add_argument("--log-results", type=bool, default=True)
    parser.add_argument("--repair-attempts", type=int, default=3)
    
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
        
    
    ######## Train Task Decomposition ########
        
    # prepare train decompostion demonstration for ai2thor samples
    prompt = f"from skills import " + IMPLEMENTED_AI2THOR_ACTIONS_TEXT
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    objects_ai = f"\n\nobjects = {get_ai2_thor_objects(args.floor_plan)}"
    prompt += objects_ai
    
    # read input train prompts
    decompose_prompt_file = open(os.getcwd() + "/data/pythonic_plans/" + args.prompt_decompse_set + ".py", "r")
    decompose_prompt = decompose_prompt_file.read()
    decompose_prompt_file.close()
    
    prompt += "\n\n" + decompose_prompt
    
    print ("Generating Decompsed Plans...")
    
    decomposed_plan = []
    for task in test_tasks:
        curr_prompt =  f"{prompt}\n\n# Task Description: {task}"
        
        if "gpt" not in args.gpt_version:
            # older gpt versions
            _, text = LM(curr_prompt, args.gpt_version, max_tokens=1000, stop=["def"], frequency_penalty=0.15)
        else:            
            messages = [{"role": "user", "content": curr_prompt}]
            _, text = LM(messages,args.gpt_version, max_tokens=1300, frequency_penalty=0.0)

        decomposed_plan.append(text)
        
    print ("Generating Allocation Solution...")

    ######## Train Task Allocation - SOLUTION ########
    prompt = f"from skills import " + IMPLEMENTED_AI2THOR_ACTIONS_TEXT
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    
    prompt_file = os.getcwd() + "/data/pythonic_plans/" + args.prompt_allocation_set + "_solution.py"
    allocated_prompt_file = open(prompt_file, "r")
    allocated_prompt = allocated_prompt_file.read()
    allocated_prompt_file.close()
    
    prompt += "\n\n" + allocated_prompt + "\n\n"
    
    allocated_plan = []
    for i, plan in enumerate(decomposed_plan):
        no_robot  = len(available_robots[i])
        curr_prompt = prompt + plan
        curr_prompt += f"\n# TASK ALLOCATION"
        curr_prompt += f"\n# Scenario: There are {no_robot} robots available, The task should be performed using the minimum number of robots necessary. Robots should be assigned to subtasks that match its skills and mass capacity. Using your reasoning come up with a solution to satisfy all contraints."
        curr_prompt += f"\n\nrobots = {available_robots[i]}"
        curr_prompt += f"\n{objects_ai}"
        curr_prompt += f"\n\n# IMPORTANT: The AI should ensure that the robots assigned to the tasks have all the necessary skills to perform the tasks. IMPORTANT: Determine whether the subtasks must be performed sequentially or in parallel, or a combination of both and allocate robots based on availablitiy. "
        curr_prompt += "\n# EXECUTION-AWARE ALLOCATION CONSTRAINTS:"
        curr_prompt += "\n# There is no TransferObject helper in this codebase."
        curr_prompt += "\n# Do not form a robot team only by taking the union of skills if execution would require one robot to hand a held object or tool to another robot."
        curr_prompt += "\n# If subtasks form a dependent sequence through the same held object or tool, assign the whole dependent sequence to one capable robot whenever such a robot exists."
        curr_prompt += "\n# Use multiple robots for independent subtasks or parallel work that does not require unsupported object handoff."
        curr_prompt += "\n# If no allocation can execute without unsupported object handoff, state that the allocation is infeasible under the current helper set."
        curr_prompt += f"\n# SOLUTION  \n"

        if "gpt" not in args.gpt_version:
            # older versions of GPT
            _, text = LM(curr_prompt, args.gpt_version, max_tokens=1000, stop=["def"], frequency_penalty=0.65)
        
        elif "gpt-3.5" in args.gpt_version:
            # gpt 3.5 and its variants
            messages = [{"role": "user", "content": curr_prompt}]
            _, text = LM(messages, args.gpt_version, max_tokens=1500, frequency_penalty=0.35)
        
        else:          
            # gpt 4.0
            messages = [{"role": "system", "content": ALLOCATION_SYSTEM_MESSAGE},{"role": "system", "content": "You are a Robot Task Allocation Expert"},{"role": "user", "content": curr_prompt}]
            _, text = LM(messages, args.gpt_version, max_tokens=400, frequency_penalty=0.69)

        allocated_plan.append(text)
    
    print ("Generating Allocated Code...")
    
    ######## Train Task Allocation - CODE Solution ########

    prompt = f"from skills import " + IMPLEMENTED_AI2THOR_ACTIONS_TEXT
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    prompt += objects_ai
    
    code_plan = []

    prompt_file1 = os.getcwd() + "/data/pythonic_plans/" + args.prompt_allocation_set + "_code.py"
    code_prompt_file = open(prompt_file1, "r")
    code_prompt = code_prompt_file.read()
    code_prompt_file.close()
    
    prompt += "\n\n" + code_prompt + "\n\n"

    for i, (plan, solution) in enumerate(zip(decomposed_plan,allocated_plan)):
        curr_prompt = prompt + plan
        curr_prompt += f"\n# TASK ALLOCATION"
        curr_prompt += f"\n\nrobots = {available_robots[i]}"
        curr_prompt += solution
        curr_prompt += "\n\n# CODE GENERATION CONSTRAINTS"
        curr_prompt += "\n# Follow the TASK ALLOCATION when it is directly executable under the helper and object ownership constraints."
        curr_prompt += "\n# If the allocation splits a dependent sequence across robots and there is no TransferObject helper, consolidate that sequence onto one capable robot."
        curr_prompt += "\n# Use only these implemented helper signatures: GoToObject(robot, object), PickupObject(robot, object), PutObject(robot, object, receptacleObject), ThrowObject(robot, object), SliceObject(robot, object), CleanObject(robot, object), OpenObject(robot, object), CloseObject(robot, object), SwitchOn(robot, object), SwitchOff(robot, object), BreakObject(robot, object)."
        curr_prompt += "\n# Do not use DropHandObject, PushObject, or PullObject because they are not implemented helper functions in data/aithor_connect/aithor_connect.py."
        curr_prompt += "\n# Never add a target receptacle argument to ThrowObject. Correct: ThrowObject(robot, object). Incorrect: ThrowObject(robot, object, receptacle)."
        curr_prompt += "\n# Preserve object ownership: if a robot picks up an object, only that same robot may later PutObject or ThrowObject that held object. There is no TransferObject helper."
        curr_prompt += "\n# Do not add PickupObject unless the object must be moved, placed into a receptacle, or explicitly held for the task."
        curr_prompt += f"\n# CODE Solution  \n"
        
        if "gpt" not in args.gpt_version:
            # older versions of GPT
            _, text = LM(curr_prompt, args.gpt_version, max_tokens=1000, stop=["def"], frequency_penalty=0.30)
        else:            
            # using variants of gpt 4 or 3.5
            messages = [{"role": "system", "content": CODE_GENERATION_SYSTEM_MESSAGE + " Strictly preserve the robot assignment from TASK ALLOCATION unless it is impossible under the provided constraints."},{"role": "user", "content": curr_prompt}]
            _, text = LM(messages, args.gpt_version, max_tokens=1400, frequency_penalty=0.4)

        code_plan.append(extract_python_code(text))
    
    # save generated plan
    exec_folders = []
    if args.log_results:
        line = {}
        now = datetime.now() # current date and time
        date_time = now.strftime("%m-%d-%Y-%H-%M-%S")
        
        for idx, task in enumerate(test_tasks):
            task_name = "{fxn}".format(fxn = '_'.join(task.split(' ')))
            task_name = task_name.replace('\n','')
            folder_name = f"{task_name}_plans_{date_time}"
            exec_folders.append(folder_name)
            
            os.mkdir("./logs/"+folder_name)
     
            with open(f"./logs/{folder_name}/log.txt", 'w') as f:
                f.write(task)
                f.write(f"\n\nGPT Version: {args.gpt_version}")
                f.write(f"\n\nFloor Plan: {args.floor_plan}")
                f.write(f"\n{objects_ai}")
                f.write(f"\nrobots = {available_robots[idx]}")
                f.write(f"\nground_truth = {gt_test_tasks[idx]}")
                f.write(f"\ntrans = {trans_cnt_tasks[idx]}")
                f.write(f"\nmax_trans = {max_trans_cnt_tasks[idx]}")

            with open(f"./logs/{folder_name}/decomposed_plan.py", 'w') as d:
                d.write(decomposed_plan[idx])
                
            with open(f"./logs/{folder_name}/allocated_plan.py", 'w') as a:
                a.write(allocated_plan[idx])
                
            with open(f"./logs/{folder_name}/code_plan.py", 'w') as x:
                x.write(code_plan[idx])

            repaired_code_plan, validation_status = repair_code_plan_if_needed(
                f"./logs/{folder_name}",
                task,
                decomposed_plan[idx],
                allocated_plan[idx],
                code_plan[idx],
                available_robots[idx],
                gt_test_tasks[idx],
                args.gpt_version,
                args.repair_attempts,
            )
            code_plan[idx] = repaired_code_plan
            print(f"Validation status for {folder_name}: {validation_status}")
            
