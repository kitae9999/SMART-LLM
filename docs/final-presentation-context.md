# SMART-LLM 최종발표 컨텍스트 정리

## 1. 이전 실행결과에서 발견한 한계

FloorPlan6 환경에서 세 작업을 각각 두 차례 실행한 결과, SMART-LLM의 핵심 한계는 "LLM이 만든 계획이 실제 시뮬레이터 실행 조건을 항상 만족하지 않는다"는 점이었다. 특히 단일 로봇 작업보다 여러 로봇이 엮이는 작업에서 문제가 뚜렷하게 나타났다.

비교한 실행 로그는 다음 두 세트다.

| 실행 회차 | 로그 timestamp |
| --- | --- |
| 1차 실행 | `04-24-2026-21-20-11` |
| 2차 실행 | `05-03-2026-16-37-09` |

테스트 작업은 FloorPlan6의 세 지침이다.

| 작업 | 확인하려던 지점 |
| --- | --- |
| `Slice the tomato` | 단일 로봇이 짧은 순서의 작업을 안정적으로 수행하는지 |
| `Wash the lettuce and place lettuce on the Countertop` | 물체를 집고, 씻고, 다시 놓는 과정에서 물체 상태가 이어지는지 |
| `Throw the Spatula in the trash` | 로봇 스킬 분담과 실제 code plan이 일치하는지 |

### 1.1 같은 지시에서도 code plan이 달라짐

같은 FloorPlan6, 같은 작업 지시를 사용해도 LLM이 생성한 code plan은 매번 동일하지 않았다.

`Slice the tomato`는 두 번 모두 한 로봇이 Knife를 집고 Tomato를 자르는 흐름으로 생성되었다. 다만 2차 실행에서는 목표 조건에는 직접 필요하지 않은 `Knife` 반환 동작이 추가되었다.

`Wash lettuce`와 `Throw spatula`에서는 차이가 더 컸다. 한 번은 단일 로봇 중심으로 생성되고, 다른 한 번은 여러 로봇을 나누어 쓰는 방향으로 생성되었다. 이 결과는 SMART-LLM의 출력이 고정된 컴파일 결과가 아니라 LLM 생성 결과라는 점을 보여준다.

발표에서 사용할 수 있는 핵심 문장:

> 같은 지시를 반복 실행해도 code plan이 달라졌고, 이 차이가 실제 실행 가능성에도 영향을 주었다.

### 1.2 allocation reasoning과 code plan 불일치

가장 중요한 한계는 allocation reasoning과 최종 code plan이 항상 일치하지 않는다는 점이다.

`Throw the Spatula in the trash` 1차 실행에서 allocation reasoning은 Robot 1과 Robot 2의 협업이 필요하다고 판단했다.

- Robot 1: `ThrowObject` 가능, `PickupObject` 불가
- Robot 2: `PickupObject` 가능, `ThrowObject` 불가
- reasoning 결론: 두 로봇의 협업 필요

하지만 최종 code plan은 로봇 하나만 사용하는 형태로 생성되었다.

```python
def throw_spatula_in_trash(robot_list):
    GoToObject(robot_list[0], 'Spatula')
    PickupObject(robot_list[0], 'Spatula')
    GoToObject(robot_list[0], 'GarbageCan')
    ThrowObject(robot_list[0], 'Spatula')

throw_spatula_in_trash([robots[1]])
```

즉, reasoning 단계에서는 협업이 필요하다고 했지만, code plan 단계에서는 그 결과가 강제되지 않았다. LLM이 앞에서 만든 추론이 뒤의 코드 생성에 제약 조건으로 적용되지 않는 구조적 문제가 있었다.

발표에서 사용할 수 있는 핵심 문장:

> SMART-LLM은 reasoning을 생성하지만, 그 reasoning이 최종 code plan에서 반드시 지켜지지는 않았다.

### 1.3 로봇 간 물체 소유권이 이어지지 않음

다중 로봇 작업에서 가장 실질적인 문제는 물체를 누가 들고 있는지 추적하지 못한다는 점이었다.

`Wash the lettuce and place lettuce on the Countertop` 2차 실행에서는 작업이 로봇 두 대로 나뉘었다.

- Robot 1: Lettuce 세척
- Robot 2: Lettuce를 CounterTop에 배치

문제는 Robot 1이 들고 있던 Lettuce가 Robot 2에게 전달되는 과정이 code plan에 없었다는 점이다. 자연어 수준에서는 "한 로봇이 씻고 다른 로봇이 놓는다"가 협업처럼 보이지만, AI2-THOR 실행에서는 물체를 실제로 들고 있는 agent가 맞아야 한다.

이와 같은 문제가 `Throw the Spatula` 2차 실행에서도 발생했다.

- Robot 2가 Spatula를 집음
- Robot 1이 GarbageCan으로 이동해 Spatula를 던지려 함
- 하지만 Robot 1은 Spatula를 들고 있지 않음

현재 코드에는 `TransferObject(from_robot, to_robot, object)` 같은 명시적인 전달 action이 없고, 실행 전에도 "이 물체를 지금 누가 들고 있는지"를 검사하지 않는다.

발표에서 사용할 수 있는 핵심 문장:

> 다중 로봇 계획에서 가장 큰 문제는 역할 분담 자체가 아니라, 물체 소유권이 로봇 사이에서 이어지지 않는다는 점이었다.

### 1.4 로봇 스킬 제약이 code plan에서 강제되지 않음

로봇별 스킬은 `resources/robots.py`에 정의되어 있고, allocation reasoning 단계에서는 이 스킬을 참고한다. 하지만 code plan 실행 단계에서는 해당 로봇이 실제로 그 action을 수행할 수 있는지 강하게 검증하지 않는다.

예를 들어 `Throw the Spatula`에서는 Robot 2가 `PickupObject`를 할 수 있고 Robot 1이 `ThrowObject`를 할 수 있기 때문에 reasoning은 협업을 선택했다. 하지만 code plan이 한 로봇에게 모든 동작을 몰아주거나, 물체를 들지 않은 로봇에게 `ThrowObject`를 시키는 상황이 발생했다.

필요한 검증은 다음과 같다.

- `PickupObject(robot, obj)` 호출 시 해당 robot이 `PickupObject` skill을 갖는지
- `ThrowObject(robot, obj)` 호출 시 해당 robot이 `ThrowObject` skill을 갖는지
- `PutObject(robot, obj, receptacle)` 호출 시 해당 robot이 obj를 들고 있는지

발표에서 사용할 수 있는 핵심 문장:

> 로봇 스킬은 prompt에는 들어가지만, 실행 전 code validation 단계에서 제약으로 강제되지는 않았다.

### 1.5 LLM에게 제공되는 action 목록과 실제 helper 구현이 일치하지 않음

프롬프트 개선 후 새로 생성한 `Throw_the_Spatula_in_the_trash_plans_06-14-2026-17-32-12` 실행에서 추가 한계가 드러났다. LLM이 생성한 code plan은 `DropHandObject`를 사용했다.

```python
def throw_spatula_in_trash(robot_list):
    GoToObject(robot_list[1], 'Spatula')
    PickupObject(robot_list[1], 'Spatula')
    GoToObject(robot_list[0], 'GarbageCan')
    DropHandObject(robot_list[1], 'GarbageCan')

throw_spatula_in_trash([robots[0], robots[1]])
```

실행 결과는 다음 오류로 실패했다.

```text
NameError: name 'DropHandObject' is not defined
```

원인은 `DropHandObject`가 `resources/actions.py`와 `resources/robots.py`에는 존재하지만, 실제 실행 helper인 `data/aithor_connect/aithor_connect.py`에는 구현되어 있지 않기 때문이다.

현재 구조는 다음처럼 나뉜다.

| 파일 | 역할 | 문제 |
| --- | --- | --- |
| `resources/actions.py` | LLM에게 제공되는 사용 가능 action 목록 | `DropHandObject`, `PushObject`, `PullObject` 포함 |
| `resources/robots.py` | 로봇별 symbolic skill 정의 | 일부 로봇 skill에 미구현 action 포함 |
| `data/aithor_connect/aithor_connect.py` | 실제 AI2-THOR 실행 helper 정의 | `DropHandObject` helper 없음 |

따라서 LLM 입장에서는 `DropHandObject`가 사용 가능한 action처럼 보이지만, 실제 실행 단계에서는 해당 함수가 없어 `NameError`가 발생한다. 이 문제는 LLM의 단순 실수라기보다, 프로젝트가 LLM에게 제공하는 action space와 실제 executor의 helper coverage가 일치하지 않는 구조적 한계다.

발표에서 사용할 수 있는 핵심 문장:

> LLM에게 제공된 action 목록에는 `DropHandObject`가 있었지만, 실제 executor에는 해당 helper가 구현되어 있지 않아 실행 중 NameError가 발생했다.

### 1.6 helper 함수 signature 오류를 실행 전 잡지 못함

`Throw the Spatula` 2차 실행에서는 생성된 code plan이 `ThrowObject`를 잘못 호출했다.

생성된 코드:

```python
ThrowObject(robot_list[0], 'Spatula', 'GarbageCan')
```

하지만 실제 helper 함수 정의는 다음과 같다.

```python
def ThrowObject(robot, sw_obj):
    ...
```

즉, 실제 함수는 인자를 2개만 받는데, LLM이 생성한 코드는 인자를 3개 전달했다. 이 오류는 실행 전에는 잡히지 않았고, 실제 실행 중 `TypeError`로 드러났다.

이 사례는 LLM 출력이 Python 코드처럼 보이더라도 실제 local helper API와 맞는지 검증해야 한다는 점을 보여준다.

발표에서 사용할 수 있는 핵심 문장:

> 생성된 code plan은 실행 가능한 Python처럼 보였지만, 실제 helper 함수 정의와 맞지 않아 런타임 오류가 발생했다.

### 1.7 task-level 성공 판정이 약함

현재 실행 파이프라인은 영상과 action 실행 로그를 남기지만, 작업 목표가 실제로 달성되었는지를 일관되게 판정하는 부분은 부족하다.

최종적으로는 다음과 같은 task-level check가 필요하다.

- `Slice the tomato`: Tomato의 `isSliced == True`
- `Wash lettuce`: Lettuce가 CounterTop 위에 존재
- `Throw spatula`: Spatula가 GarbageCan 안에 존재

영상으로 보면 그럴듯해 보여도, 실제 객체 metadata 기준으로 목표 상태가 달성되었는지 확인해야 한다. 최종 발표에서는 이 부분을 "실험 자동 평가 체계의 필요성"으로 연결할 수 있다.

발표에서 사용할 수 있는 핵심 문장:

> 실행 영상만으로는 성공 여부를 판단하기 어렵기 때문에, 객체 metadata 기반의 task-level success check가 필요하다.

### 1.8 실행 재현성이 약함

`data/aithor_connect/aithor_connect.py`에서는 agent 초기 위치를 `random.choice(reachable_positions_)`로 정한다. 따라서 같은 code plan을 실행하더라도 시작 위치가 달라질 수 있다.

이 문제는 연구 실험 관점에서 재현성을 떨어뜨린다. 같은 작업을 비교하려면 다음 정보가 고정되거나 로그에 남아야 한다.

- random seed
- 각 agent의 초기 위치
- 선택된 objectId
- 실행 중 action sequence

발표에서 사용할 수 있는 핵심 문장:

> 같은 계획을 비교하려면 LLM 출력뿐 아니라 시뮬레이터 초기 조건도 고정하거나 기록해야 한다.

### 1.9 validator 실패가 곧 task infeasible을 의미하지는 않음

`Wash_the_lettuce_and_place_lettuce_on_the_Countertop_plans_06-16-2026-17-44-31` 로그에서 추가로 확인한 점은, validator가 막은 plan이 항상 "현재 helper로 불가능한 task"를 뜻하지는 않는다는 것이다.

해당 code plan은 다음처럼 생성되었다.

```python
wash_lettuce([robots[0], robots[2]])
place_lettuce_on_countertop(robots[1])
```

세척 함수 내부에서는 `robot1`이 Lettuce를 집고 씻은 뒤 다시 집는다. 하지만 최종 배치 함수는 `robot2`가 Lettuce를 CounterTop에 놓으려고 한다.

```text
Plan validation failed:
- line 27: [error] robot2 tries to PutObject Lettuce, but is holding nothing.
```

이 경우는 Spatula 사례와 다르다. `robot1`은 `PickupObject`, `PutObject`, `SwitchOn`, `SwitchOff`를 모두 가지고 있으므로, 한 로봇이 세척부터 배치까지 수행하는 정상 plan을 만들 수 있었다. 즉 이 실패는 task 자체가 불가능한 것이 아니라, LLM이 실행 가능한 단일 로봇 plan 대신 불필요하게 역할을 나누면서 발생한 code generation 실패다.

따라서 validator는 단순히 실패를 막는 것에서 끝나면 안 되고, 실패 원인을 구분해야 한다.

- `INFEASIBLE`: 현재 helper와 robot skill로는 수행 불가능
- `REPAIRABLE_PLAN_ERROR`: task는 가능하지만 LLM이 잘못된 code plan 생성

발표에서 사용할 수 있는 핵심 문장:

> validator 실패는 task 불가능과 동일하지 않다. Lettuce 사례처럼 실행 가능한 작업도 LLM이 물체 소유권을 잘못 나누면 실패하므로, 실패 원인을 구조화해서 재생성 또는 수정에 활용해야 한다.

## 2. 한계 요약

이전 실행 결과에서 발견한 한계는 다음 일곱 가지로 압축할 수 있다.

1. 같은 지시에서도 LLM이 생성하는 code plan이 달라진다.
2. allocation reasoning이 최종 code plan에 강제되지 않는다.
3. 다중 로봇 작업에서 물체 소유권이 이어지지 않는다.
4. LLM에게 제공되는 action/skill 목록과 실제 helper 구현이 일치하지 않는다.
5. robot skill과 helper 함수 signature 검증이 부족하다.
6. 실행 결과를 task-level success로 자동 판정하는 체계가 약하다.
7. validator 실패가 task infeasible인지, 수정 가능한 code plan 오류인지 구분되어야 한다.

최종 발표의 핵심 메시지는 다음처럼 잡을 수 있다.

> SMART-LLM은 자연어 지시를 로봇 action plan으로 변환하는 가능성을 보여주지만, 다중 로봇 실행에서는 reasoning-code 일치성, 물체 소유권, 실제 helper coverage, 함수 호출 검증이 없으면 실행 단계에서 쉽게 깨진다.

## 3. 현재까지 적용한 보완

위 한계를 바탕으로 현재 코드에 일부 보완을 적용했다. 핵심 방향은 LLM이 생성하는 plan의 품질을 프롬프트에서 먼저 제한하고, 그래도 잘못 생성된 code plan은 실행 전에 validator로 차단하는 것이다.

### 3.1 LLM에게 제공하는 action 목록을 실제 helper 기준으로 제한

기존에는 `resources/actions.py`의 action 목록이 그대로 프롬프트에 들어갔다. 이 목록에는 `DropHandObject`, `PushObject`, `PullObject`처럼 실제 helper로 구현되지 않은 action도 포함되어 있었다.

이를 줄이기 위해 `scripts/run_llm.py`에 실제 구현된 helper 기준의 action 목록을 별도로 정의했다.

```python
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
```

이후 task decomposition, allocation reasoning, code generation 프롬프트에서 이 목록을 사용하도록 변경했다.

적용 의미:

> LLM에게 처음부터 실제 실행 가능한 helper 목록만 제공해 미구현 action이 생성될 가능성을 줄였다.

### 3.2 로봇 skill 목록도 구현된 helper 기준으로 필터링

`resources/robots.py`의 일부 로봇 skill에는 실제 helper가 없는 action이 포함되어 있었다. 그래서 `scripts/run_llm.py`에서 task별 robot list를 구성할 때, 구현된 helper 이름에 포함되는 skill만 남기도록 했다.

```python
rob = copy.deepcopy(robots.robots[r_id-1])
rob['skills'] = [skill for skill in rob['skills'] if skill in IMPLEMENTED_ACTION_NAMES]
```

적용 의미:

> allocation reasoning 단계에서 LLM이 미구현 skill을 로봇 능력으로 착각하지 않도록 입력 데이터를 정리했다.

### 3.3 code generation 프롬프트 제약 추가

`scripts/run_llm.py`의 code generation 요청에 다음 제약을 추가했다.

- `TASK ALLOCATION`에서 정한 로봇 배정을 따를 것
- team allocation을 단일 로봇 코드로 축소하지 말 것
- 구현된 helper signature만 사용할 것
- `DropHandObject`, `PushObject`, `PullObject`를 사용하지 말 것
- `ThrowObject(robot, object)`에 target receptacle 인자를 추가하지 말 것
- 물체를 집은 로봇이 이후 `PutObject`, `ThrowObject` 같은 조작도 수행할 것
- `TransferObject` helper는 없다고 명시

또 code generation system message도 "실행 가능한 Python 코드만 반환"하도록 강화했다.

적용 의미:

> LLM이 reasoning과 다른 code plan을 만들거나 helper signature를 잘못 쓰는 문제를 줄이도록 프롬프트를 보강했다.

### 3.4 few-shot code 예제의 잘못된 helper 호출 수정

`data/pythonic_plans/train_task_allocation_code.py` 상단에 code generation rule을 추가했고, 기존 예제 중 `ThrowObject`를 잘못 호출하던 부분을 수정했다.

기존 예제에는 다음과 같은 잘못된 호출이 있었다.

```python
ThrowObject(robot_list,'Fork', 'GarbageCan')
```

실제 helper 정의는 `ThrowObject(robot, object)`이므로 다음처럼 수정했다.

```python
ThrowObject(robot_list[0],'Fork')
```

적용 의미:

> LLM이 잘못된 few-shot 예제를 따라 `ThrowObject`에 인자를 3개 넣는 문제를 줄였다.

### 3.5 code plan 실행 전 validator 추가

`scripts/validate_plan.py`를 새로 추가했다. 이 validator는 생성된 `code_plan.py`를 AST로 파싱해서 실행 전에 다음 항목을 검사한다.

| 검사 항목 | 오류 코드 | 예시 |
| --- | --- | --- |
| helper 존재 여부 | `UNIMPLEMENTED_HELPER` | `DropHandObject(...)` |
| helper 인자 개수 | `HELPER_SIGNATURE_MISMATCH` | `ThrowObject(robot, obj, receptacle)` |
| robot skill 보유 여부 | `ROBOT_SKILL_MISMATCH` | `robot2`가 `ThrowObject` 수행 |
| object ownership | `OBJECT_OWNERSHIP_MISMATCH` | `robot2`가 집은 물체를 `robot1`이 throw |

최신 `Throw_the_Spatula_in_the_trash_plans_06-16-2026-17-44-31` 로그는 validator에서 다음 오류로 차단되었다.

```text
Plan validation failed:
- line 11: [error] OBJECT_OWNERSHIP_MISMATCH: robot1 tries to ThrowObject Spatula, but is holding nothing.
  suggestion: robot2 holds Spatula, but lacks ThrowObject. This plan requires object handoff; add a TransferObject helper or mark the plan infeasible.
```

적용 의미:

> LLM이 생성한 code plan이 Python 문법상 실행 가능해 보여도, robot skill과 물체 소유권을 만족하지 않으면 AI2-THOR 실행 전에 실패로 처리할 수 있게 되었다.

### 3.6 `execute_plan.py`에 validator 연동

기존 `scripts/execute_plan.py`는 `code_plan.py`를 `executable_plan.py`에 붙인 뒤 바로 실행했다. 이제는 실행 전에 validator를 먼저 호출한다.

```python
validation_issues = validate_log_plan(os.getcwd() + "/logs/" + expt_name)
print_issues(validation_issues)
if any(issue.severity == "error" for issue in validation_issues):
    raise SystemExit(1)
```

적용 의미:

> 잘못된 plan은 시뮬레이터를 실행하기 전에 멈추므로, 실패 원인을 더 빠르게 확인할 수 있다.

### 3.7 validation report 저장 및 실패 원인 구조화

validator의 실패 메시지를 단순 문장 출력에서 구조화된 오류 정보로 확장했다.

각 `ValidationIssue`는 다음 정보를 가진다.

- `code`: 오류 유형
- `line`: 오류 발생 줄
- `message`: 사람이 읽을 수 있는 설명
- `suggestion`: 수정 방향
- `details`: actor, object, current holder, candidate robot 같은 부가 정보

또 `scripts/validate_plan.py`와 `scripts/execute_plan.py` 모두 각 로그 폴더에 `validation_report.txt`를 저장하도록 했다.

예를 들어 Lettuce 실패는 다음처럼 기록된다.

```text
Plan validation failed:
- line 27: [error] OBJECT_OWNERSHIP_MISMATCH: robot2 tries to PutObject Lettuce, but is holding nothing.
  suggestion: Use robot1 for PutObject(Lettuce), or add an explicit TransferObject helper before this action.
```

적용 의미:

> 실패 원인을 LLM에게 다시 물어보지 않고, validator 코드가 직접 분류할 수 있게 되었다. 이 정보는 이후 code plan 재생성 요청이나 자동 수정 후보 판단에 사용할 수 있다.

### 3.8 `INFEASIBLE`과 `REPAIRABLE_PLAN_ERROR` 분류 추가

validator 결과를 단순 성공/실패로 보지 않고, 실패 원인을 기준으로 다음 상태로 분류하도록 했다.

| 상태 | 의미 |
| --- | --- |
| `PASS` | 실행 전 정적 검증 통과 |
| `REPAIRABLE_PLAN_ERROR` | task는 수행 가능해 보이지만 LLM이 잘못된 code plan 생성 |
| `INFEASIBLE` | 현재 helper와 robot skill로는 수행 불가능 |

분류 기준은 validator가 가진 구조화된 오류 정보를 사용한다.

- holder가 target action skill을 가지고 있으면 `REPAIRABLE_PLAN_ERROR`
- 같은 action을 수행할 수 있는 다른 로봇이 있으면 `REPAIRABLE_PLAN_ERROR`
- 어느 로봇도 필요한 skill을 갖지 않으면 `INFEASIBLE`
- object handoff가 필요하지만 `TransferObject`가 없으면 기본적으로 `INFEASIBLE`
- 단, goal이 `receptacle contains object`이면 `PutObject` 대체 가능성을 검사

적용 의미:

> validator 실패를 바로 task 실패로 보지 않고, 수정 가능한 plan 오류와 실제 실행 불가능 케이스를 구분할 수 있게 되었다.

### 3.9 goal 기반 대체 action 판단 추가

`Throw the Spatula in the trash`처럼 자연어에는 `throw`가 들어가지만 ground truth goal은 다음처럼 containment인 경우가 있다.

```python
ground_truth = [{'name': 'GarbageCan', 'contains': ['Spatula'], 'state': 'None'}]
```

이 경우 `ThrowObject`를 반드시 사용하지 않아도, `PutObject(robot, 'Spatula', 'GarbageCan')`로 목표 상태를 만족할 수 있다. validator는 object ownership 오류가 발생했을 때 다음 조건을 확인한다.

- 현재 object holder가 누구인지
- holder가 `PutObject` skill을 가지고 있는지
- ground truth에 `contains` goal이 있는지

조건을 만족하면 다음처럼 repair suggestion을 만든다.

```text
Goal alternative available: the goal is GarbageCan contains Spatula.
Use PutObject(robot2, Spatula, GarbageCan) instead of requiring ThrowObject or handoff.
```

적용 의미:

> 자연어 action 이름보다 최종 목표 상태를 우선해, 실행 가능한 대체 action을 찾을 수 있게 되었다.

### 3.10 `run_llm.py` 생성 직후 재생성 루프 추가

`scripts/run_llm.py`에서 plan을 생성하고 로그 폴더에 저장한 뒤, 각 plan에 대해 validator를 자동 실행하도록 했다.

흐름은 다음과 같다.

```text
run_llm.py --floor-plan 6
→ decomposed_plan 생성
→ allocated_plan 생성
→ code_plan 생성
→ validator 실행
→ PASS면 저장 완료
→ REPAIRABLE_PLAN_ERROR면 validation feedback으로 code_plan 재생성
→ 재검증
→ INFEASIBLE이면 재생성하지 않고 report 저장
```

재생성 프롬프트에는 다음 정보가 들어간다.

- task description
- ground truth goal
- available robots
- decomposed plan
- allocated plan
- invalid code plan
- validation report
- repair constraints

재생성은 기본 3회 수행하며, 옵션으로 조정할 수 있다.

```bash
python3 scripts/run_llm.py --floor-plan 6 --repair-attempts 3
```

적용 의미:

> validator가 단순 차단 장치에서 끝나지 않고, 수정 가능한 code plan은 최대 3회까지 자동 재생성하도록 연결되었다.

### 3.11 repair loop 실행 중 발견한 추가 보완

`FloorPlan6`을 다시 생성한 결과, repair loop가 동작하면서 두 가지 추가 문제가 확인되었다.

첫째, `Slice the tomato` plan은 실제로는 불가능한 작업이 아니었다. 기존 validator는 `robot1`이 Knife를 든 상태에서 Tomato를 다시 집으려는 `HAND_NOT_EMPTY` 오류와, Tomato를 들지 않은 상태에서 `PutObject(Tomato)`를 호출하는 오류를 보고 `INFEASIBLE`로 분류했다.

하지만 이 작업의 ground truth는 Tomato의 상태가 `SLICED`가 되는 것이다.

```python
ground_truth = [{'name': 'Tomato', 'contains': [], 'state': 'SLICED'}]
```

따라서 `PutObject(Tomato, CounterTop)`은 목표 달성에 필수적인 동작이 아니며, 잘못 생성된 부가 동작으로 보는 것이 맞다. 이를 반영해 state goal이 있는 object에 대한 불필요한 `PutObject` 오류는 `REPAIRABLE_PLAN_ERROR`로 분류하도록 보완했다.

둘째, repair regeneration 과정에서 LLM이 decomposed plan의 robot 없는 helper 호출을 따라가면서 다음처럼 잘못된 code plan을 생성했다.

```python
GoToObject('Lettuce')
PickupObject('Lettuce')
PutObject('Lettuce', 'Sink')
```

실제 helper signature는 항상 robot 인자를 첫 번째로 요구한다.

```python
GoToObject(robot, object)
PickupObject(robot, object)
PutObject(robot, object, receptacleObject)
```

이를 줄이기 위해 repair prompt에 다음 제약을 추가했다.

- decomposed plan의 robot 없는 helper 호출을 복사하지 말 것
- 모든 helper 호출은 robot 인자를 첫 번째로 포함할 것
- 올바른 helper 호출 예시와 잘못된 예시를 함께 제공
- task 함수는 `robot_list` 또는 `robot` 인자를 받고, 호출부에서 `robots[index]`를 넘길 것

적용 의미:

> repair loop를 추가해도 LLM 출력은 다시 잘못될 수 있으므로, validator와 repair prompt는 함께 강화되어야 한다. 특히 decomposition 단계의 no-robot pseudo code와 execution 단계의 robot-aware helper signature를 명확히 구분해야 한다.

### 3.12 object ownership 프롬프트 범위 축소

보완 이후 `Slice the tomato`가 오히려 실패한 원인을 다시 보면, 기존 프롬프트의 object ownership 문구가 너무 넓었다.

기존 문구는 다음과 같았다.

```text
The robot that picks up an object must be the robot that later puts, drops, throws, or uses that held object.
```

여기서 `uses`라는 표현이 넓어서, LLM이 `SliceObject(robot, 'Tomato')`를 수행하려면 Tomato도 먼저 집어야 한다고 해석했을 가능성이 있다. 실제로 새 로그에서는 Knife를 집은 뒤 Tomato까지 집으려 하면서 `HAND_NOT_EMPTY` 오류가 발생했다.

이를 막기 위해 ownership 규칙을 다음처럼 좁혔다.

```text
If a robot picks up an object, only that same robot may later PutObject or ThrowObject that held object.
Do not add PickupObject unless the object must be moved, placed into a receptacle, or explicitly held for the task.
```

적용 의미:

> 특정 `SliceObject` 규칙을 추가하지 않고, 불필요한 `PickupObject` 생성을 줄이는 일반 원칙으로 수정했다.

### 3.13 state goal 기반 `HAND_NOT_EMPTY` repair 피드백 보완

`06-16-2026-19-34-23` 실행에서는 `Wash the lettuce`와 `Throw the Spatula`는 repair 이후 `PASS`가 되었지만, `Slice the tomato`는 repair 이후에도 실패했다.

실패한 repaired code plan은 다음 흐름이었다.

```python
PickupObject(robot_list[0], 'Knife')
GoToObject(robot_list[0], 'Tomato')
PickupObject(robot_list[0], 'Tomato')
SliceObject(robot_list[0], 'Tomato')
```

문제는 `robot1`이 이미 Knife를 들고 있는데 Tomato까지 다시 집으려고 한 것이다.

```text
HAND_NOT_EMPTY: robot1 tries to pick up Tomato, but is already holding Knife.
```

이전 validator 피드백은 `Knife를 내려놓고 Tomato를 집으라`는 방향으로 해석될 수 있었다. 하지만 이 task의 ground truth는 Tomato를 특정 receptacle로 옮기는 것이 아니라 Tomato 상태를 `SLICED`로 바꾸는 것이다.

```python
ground_truth = [{'name': 'Tomato', 'contains': [], 'state': 'SLICED'}]
```

따라서 올바른 repair 방향은 Knife를 내려놓는 것이 아니라, 불필요한 `PickupObject(Tomato)`를 제거하는 것이다. 이를 위해 validator의 `HAND_NOT_EMPTY` 분류에서 ground truth를 함께 확인하도록 보완했다.

보완 후 같은 로그를 다시 검증하면 다음 repair suggestion이 나온다.

```text
Remove unnecessary PickupObject(Tomato). The final goal changes Tomato's state to SLICED, and does not require moving it into a receptacle or holding it.
```

적용 의미:

> 특정 `SliceObject` 전용 규칙을 넣지 않고, final goal이 state change인지 containment인지에 따라 불필요한 pickup을 구분하도록 개선했다.

### 3.14 `06-16-2026-19-43-33` 재생성 결과

프롬프트와 validator 보완 이후 `FloorPlan6`을 다시 생성한 결과, 세 작업 모두 최초 code plan은 validator에서 걸렸고 repair loop 1회 안에서 최종 `PASS`까지 도달했다.

```text
Slice_the_tomato: REPAIRABLE_PLAN_ERROR -> PASS
Wash_the_lettuce_and_place_lettuce_on_the_Countertop: REPAIRABLE_PLAN_ERROR -> PASS
Throw_the_Spatula_in_the_trash: REPAIRABLE_PLAN_ERROR -> PASS
```

작업별 repair 내용은 다음과 같다.

- `Slice the tomato`: Knife를 들고 있는 robot과 Knife를 내려놓는 robot이 달라진 ownership 오류를 repair
- `Wash the lettuce`: Lettuce를 들고 있지 않은 robot이 `PutObject`를 수행하려던 ownership 오류를 repair
- `Throw the Spatula`: `ThrowObject`와 handoff 대신, goal alternative인 `PutObject(robot2, Spatula, GarbageCan)`로 repair

다만 `Slice the tomato`의 repaired code plan은 다음 구조로 생성되었다.

```python
PickupObject(robot_list[0], 'Knife')
GoToObject(robot_list[1], 'Tomato')
PickupObject(robot_list[1], 'Tomato')
SliceObject(robot_list[1], 'Tomato')
PutObject(robot_list[0], 'Knife', 'CounterTop')
```

이 코드는 현재 validator 기준으로는 `PASS`지만, 실제 AI2-THOR 실행까지 성공한다고 단정하기는 어렵다. validator가 아직 `SliceObject`의 tool precondition, 즉 Knife를 들고 있는 robot이 slicing을 수행해야 하는지까지는 모델링하지 않기 때문이다.

적용 의미:

> 현재 validator는 helper signature, robot skill, object ownership 문제는 잘 잡지만, action별 precondition/effect schema는 아직 부족하다. 따라서 `PASS`는 "정적 검증 통과"이지 "시뮬레이션 성공 보장"은 아니다.

### 3.15 `06-16-2026-19-47-33` 실제 실행 결과 분석

이후 다시 생성한 `06-16-2026-19-47-33` plan은 세 작업 모두 validator 기준 `PASS`였고, 실행 후 각 로그 디렉토리에 agent view와 top view 이미지가 생성되었다.

```text
Slice_the_tomato: PASS, top_view 125 frames
Wash_the_lettuce_and_place_lettuce_on_the_Countertop: PASS, top_view 75 frames
Throw_the_Spatula_in_the_trash: PASS, top_view 72 frames
```

각 code plan의 의미는 다음과 같다.

첫째, `Slice the tomato`는 이전 plan과 달리 하나의 로봇이 Knife를 집고 Tomato 위치로 이동한 뒤 `SliceObject`를 수행한다.

```python
GoToObject(robot_list[0], 'Knife')
PickupObject(robot_list[0], 'Knife')
GoToObject(robot_list[0], 'Tomato')
SliceObject(robot_list[0], 'Tomato')
PutObject(robot_list[0], 'Knife', 'CounterTop')
```

이전 `19-43-33` plan에서는 robot1이 Knife를 들고 robot2가 Tomato를 slice하려는 논리 오류가 있었지만, 이번 plan에서는 Knife를 든 robot과 `SliceObject`를 수행하는 robot이 일치한다. 따라서 이전에 남아 있던 semantic error가 해소된 것으로 볼 수 있다.

둘째, `Wash the lettuce`는 robot3 하나가 전체 흐름을 수행한다.

```python
PickupObject(robot, 'Lettuce')
PutObject(robot, 'Lettuce', 'Sink')
SwitchOn(robot, 'Faucet')
SwitchOff(robot, 'Faucet')
PickupObject(robot, 'Lettuce')
PutObject(robot, 'Lettuce', 'CounterTop')
```

이 plan은 다중 로봇 협업은 아니지만, 현재 helper 제약 안에서는 자연스러운 단일 로봇 plan이다. 이전처럼 Lettuce를 들지 않은 다른 로봇이 `PutObject`를 수행하는 ownership 오류는 발생하지 않는다.

셋째, `Throw the Spatula`는 `ThrowObject` 대신 `PutObject`를 사용해 목표를 달성하는 형태로 생성되었다.

```python
GoToObject(robot_list[1], 'Spatula')
PickupObject(robot_list[1], 'Spatula')
GoToObject(robot_list[1], 'GarbageCan')
PutObject(robot_list[1], 'Spatula', 'GarbageCan')
```

원래 task 문장은 "throw"지만 ground truth는 `GarbageCan`이 `Spatula`를 포함하는 것이다. 따라서 현재 helper와 robot skill 제약에서는 `ThrowObject`를 고집하기보다 `PutObject`로 goal을 만족시키는 것이 더 안정적인 repair 방향이다.

주의할 점은 실행 stdout이 로그 파일로 저장되지 않는다는 점이다. 현재 확인 가능한 자료는 `validation_report.txt`, 최종 `code_plan.py`, 실행 중 저장된 이미지 프레임이다. 따라서 이번 결과는 "실행 중 명시적 에러 없이 영상 산출물이 생성되었고, plan 구조도 논리적으로 타당해졌다"로 정리하는 것이 적절하다. 최종 성공 여부를 더 엄밀히 평가하려면 실행 종료 시 AI2-THOR metadata에서 ground truth를 자동 확인하는 task-level success evaluator가 필요하다.

### 3.16 토마토 plan 회귀 원인

추가 실행에서 `Slice the tomato`가 다시 이상한 plan으로 생성되는 문제가 확인되었다. 보완 전 로그에서는 토마토 작업이 대부분 다음처럼 안정적인 단일 로봇 plan으로 생성되었다.

```python
GoToObject(robot_list[0], 'Knife')
PickupObject(robot_list[0], 'Knife')
GoToObject(robot_list[0], 'Tomato')
SliceObject(robot_list[0], 'Tomato')
```

하지만 보완 후 일부 로그에서는 다음처럼 Knife, Tomato, SliceObject가 서로 다른 로봇으로 나뉘었다.

```python
GoToObject(robot_list[0], 'Knife')
PickupObject(robot_list[0], 'Knife')
GoToObject(robot_list[1], 'Tomato')
PickupObject(robot_list[1], 'Tomato')
SliceObject(robot_list[2], 'Tomato')
```

이 회귀의 원인은 토마토 작업 자체가 어려워진 것이 아니라, code generation prompt에 추가했던 allocation 보존 제약이 너무 강했기 때문이다.

문제가 된 방향:

```text
Strictly follow the TASK ALLOCATION above.
If TASK ALLOCATION assigns a team, the CODE Solution must use that same team; do not collapse it into one robot.
```

이 제약은 allocation reasoning과 code plan을 일치시키기 위한 것이었지만, `Slice the tomato`처럼 tool을 들고 이어서 작업해야 하는 dependent sequence에서는 역효과가 났다. allocation 단계에서 subtask가 여러 로봇으로 나뉘면, code generation 단계가 이를 억지로 유지하면서 실제로는 handoff가 필요한 plan을 만들 수 있다.

따라서 프롬프트를 다음 방향으로 수정했다.

```text
Follow the TASK ALLOCATION when it is directly executable under the helper and object ownership constraints.
If the allocation splits a dependent sequence across robots and there is no TransferObject helper, consolidate that sequence onto one capable robot.
```

적용 의미:

> allocation-code 일치 자체는 필요하지만, 실행 불가능한 allocation까지 그대로 보존하면 오히려 plan 품질이 떨어진다. 현재 구조에서는 "allocation을 무조건 따르기"보다 "실행 가능한 범위에서 따르기"가 더 적절하다.

### 3.17 allocation 단계 프롬프트 보강

토마토 plan 회귀의 근본 원인은 code generation만이 아니라, allocation 단계에서 held object/tool 의존성을 충분히 고려하지 못한 데 있었다. 따라서 code plan에서만 수습하는 방식에 더해, allocation reasoning 단계에도 실행 가능성 제약을 추가했다.

추가한 핵심 제약은 다음과 같다.

```text
There is no TransferObject helper in this codebase.
Do not form a robot team only by taking the union of skills if execution would require one robot to hand a held object or tool to another robot.
If subtasks form a dependent sequence through the same held object or tool, assign the whole dependent sequence to one capable robot whenever such a robot exists.
Use multiple robots for independent subtasks or parallel work that does not require unsupported object handoff.
If no allocation can execute without unsupported object handoff, state that the allocation is infeasible under the current helper set.
```

적용 위치:

- `scripts/run_llm.py`의 allocation prompt 본문
- `scripts/run_llm.py`의 allocation system message
- `data/pythonic_plans/train_task_allocation_solution.py` 상단 few-shot allocation rule

적용 의미:

> coalition을 금지하는 것이 아니라, 현재 helper set으로 실제 실행 가능한 coalition만 생성하도록 allocation 단계부터 제한한다.

### 3.18 영상 생성 전 종료 시퀀스 보완

`video_top_view.mp4`에서 마지막 행동이 조금 잘려 보이는 문제가 있었다. 원인은 `end_thread.py`에서 `Done` action을 큐에 넣은 직후 `task_over = True`로 실행 thread를 종료시키고, 곧바로 `generate_video()`를 호출하는 구조였다.

기존 흐름:

```text
Done action 추가
task_over = True
generate_video()
```

이 구조에서는 `exec_actions()` thread가 마지막 action을 처리하고 프레임을 저장하기 전에 영상 변환이 시작될 수 있다. 이를 줄이기 위해 종료 시퀀스를 다음처럼 수정했다.

```python
while len(action_queue) > 0:
    time.sleep(0.1)

task_over = True
actions_thread.join(timeout=5)
time.sleep(1)
```

적용 의미:

> action queue가 비워지고 프레임 저장 thread가 종료될 시간을 확보한 뒤 영상을 생성하도록 변경했다. 따라서 마지막 행동 직후 프레임이 mp4에 누락될 가능성을 줄였다.

### 3.19 `PutObject` helper의 object-distance 매칭 버그 수정

`Wash lettuce` 실행 중 같은 code plan이 어떤 실행에서는 성공하고, 어떤 실행에서는 `No valid positions to place object found`로 실패하는 문제가 있었다. 원인은 `PutObject` helper가 receptacle을 고를 때 object id 목록만 `set()`으로 변환해 순서를 섞고, distance와 center 목록은 원래 metadata 순서로 유지한 데 있었다.

기존 코드:

```python
objs = list(set([obj["objectId"] for obj in c.last_event.metadata["objects"]]))
objs_center = list([obj["axisAlignedBoundingBox"]["center"] for obj in c.last_event.metadata["objects"]])
objs_dists = list([obj["distance"] for obj in c.last_event.metadata["objects"]])
```

이 구조에서는 `objs[idx]`와 `objs_dists[idx]`가 같은 object를 가리킨다는 보장이 없다. 따라서 `Sink`를 찾더라도 실제로는 다른 object의 distance를 기준으로 receptacle을 선택할 수 있다.

수정 후에는 같은 metadata object에서 `objectId`, `distance`, `center`를 함께 읽도록 변경했다.

```python
objs = c.last_event.metadata["objects"]

for obj in objs:
    obj_id = obj["objectId"]
    match = re.match(recp, obj_id)
    if match is not None:
        dist = obj["distance"]
        if dist < dist_to_recp:
            recp_obj_id = obj_id
            dest_obj_center = obj["axisAlignedBoundingBox"]["center"]
            dist_to_recp = dist
```

적용 의미:

> 동일한 code plan이 실행마다 다르게 실패하던 원인 중 하나가 LLM planning이 아니라 executor helper 구현의 비결정성임을 확인했고, receptacle 선택이 metadata 순서에 일관되게 맞도록 보완했다.

### 3.20 repair 재시도 횟수 3회로 확대

초기 repair loop는 `REPAIRABLE_PLAN_ERROR`가 발생하면 code plan을 한 번만 재생성했다. 하지만 `Slice the tomato`와 `Throw the Spatula` 사례에서 확인했듯이, 첫 번째 repair가 기존 오류를 고치면서 다른 ownership 오류나 skill 오류를 새로 만들 수 있었다.

예를 들어 `Throw the Spatula`에서는 처음에는 `ThrowObject`를 수행할 수 없는 로봇에게 action이 배정되었고, repair 이후에는 `ThrowObject` 가능한 로봇으로 바뀌었지만 해당 로봇이 Spatula를 들고 있지 않은 문제가 다시 발생했다. 이런 경우 한 번의 repair로는 충분하지 않다.

이를 반영해 `scripts/run_llm.py`의 기본 repair 시도 횟수를 1회에서 3회로 늘렸다.

```python
parser.add_argument("--repair-attempts", type=int, default=3)
```

적용 의미:

> repair가 한 번에 끝나지 않는 경우에도 validator feedback을 반복 반영할 수 있게 되어, 단순한 code generation 오류가 최종 실패로 남을 가능성을 줄였다.

### 3.21 FloorPlan6 최신 생성 및 실행 결과

최신 FloorPlan6 생성 로그 `06-17-2026-02-05-30`에서는 세 작업이 모두 validator 기준 `PASS`가 되었고, 실제 실행 후 agent view와 top view 영상이 생성되었다.

| 작업 | validation | 실행 산출물 | 해석 |
| --- | --- | --- | --- |
| `Slice the tomato` | `PASS` | `video_top_view.mp4`, agent 영상 | robot1이 Knife를 들고 Tomato를 slice |
| `Wash the lettuce and place lettuce on the Countertop` | `PASS` | `video_top_view.mp4`, agent 영상 | robot1이 세척부터 CounterTop 배치까지 수행 |
| `Throw the Spatula in the trash` | `PASS` | `video_top_view.mp4`, agent 영상 | `ThrowObject` 대신 `PutObject(Spatula, GarbageCan)`로 goal 달성 |

최종 code plan은 다음처럼 이전 오류가 제거된 형태였다.

`Slice the tomato`:

```python
GoToObject(robot, 'Knife')
PickupObject(robot, 'Knife')
GoToObject(robot, 'Tomato')
SliceObject(robot, 'Tomato')
GoToObject(robot, 'CounterTop')
PutObject(robot, 'Knife', 'CounterTop')
```

`Wash the lettuce`:

```python
PickupObject(robot, 'Lettuce')
PutObject(robot, 'Lettuce', 'Sink')
SwitchOn(robot, 'Faucet')
SwitchOff(robot, 'Faucet')
PickupObject(robot, 'Lettuce')
PutObject(robot, 'Lettuce', 'CounterTop')
```

`Throw the Spatula`:

```python
GoToObject(robot_list[1], 'Spatula')
PickupObject(robot_list[1], 'Spatula')
GoToObject(robot_list[1], 'GarbageCan')
PutObject(robot_list[1], 'Spatula', 'GarbageCan')
```

확인된 영상 길이는 다음과 같다.

| 작업 | top view 영상 길이 |
| --- | --- |
| `Slice the tomato` | 6.88초 |
| `Wash the lettuce and place lettuce on the Countertop` | 5.04초 |
| `Throw the Spatula in the trash` | 5.04초 |

주의할 점은 현재 `SR`, `TC`, `GCR`, `Exec`, `RU` 같은 평가 지표가 터미널에만 출력되고 파일로 저장되지는 않는다는 점이다. 따라서 문서상으로는 "validator 통과, 실행 중 명시적 오류 없음, 영상 산출물 생성"으로 정리하는 것이 정확하다.

적용 의미:

> FloorPlan6의 기존 실패 사례는 action 목록 정리, allocation/code prompt 보완, validator, repair loop, executor helper 수정 이후 최신 실행에서 모두 정상 흐름으로 개선되었다.

### 3.22 FloorPlan21 데이터 파싱 보완 및 coalition 성공 사례

로봇 coalition을 확인하기 위해 FloorPlan21을 실행하려 했을 때, task 파일 파싱 단계에서 다음 오류가 발생했다.

```text
json.decoder.JSONDecodeError: Expecting value
```

원인은 `data/final_test/FloorPlan21.json`이 파일명은 `.json`이지만 실제로는 줄 단위 task record를 담은 JSONL 형태이고, 일부 줄에 JSON 표준의 `null` 대신 Python literal인 `None`이 들어 있었기 때문이다.

예:

```python
"state": None
```

기존 코드는 `json.loads(line)`만 사용했기 때문에 `None`을 읽지 못했다. 이를 보완하기 위해 task record 파서를 추가했다.

```python
try:
    record = json.loads(line)
except json.JSONDecodeError:
    record = ast.literal_eval(line)
```

또 일부 task record에는 `trans`, `max_trans`가 없을 수 있어 기본값 0을 사용하도록 했다.

```python
"trans": record.get("trans", 0)
"max_trans": record.get("max_trans", 0)
```

이후 FloorPlan21은 6개 task로 정상 파싱되었다.

```text
Put mug in coffee machine
Chill the apple and wash the knife
Put two vegetables in the fridge parallely
Wash the fork and put it in the bowl
Toast a slice of the breadloaf
Slice apple and throw it in the trash
```

`06-17-2026-02-14-57` 생성 결과는 다음과 같다.

| 작업 | 최종 상태 | 해석 |
| --- | --- | --- |
| `Put mug in coffee machine` | `PASS` | 단일 물체 배치 성공 |
| `Chill the apple and wash the knife` | `PASS` | 독립 작업 조합 통과 |
| `Put two vegetables in the fridge parallely` | `PASS` | repair 후 coalition task 통과 |
| `Wash the fork and put it in the bowl` | `PASS` | 세척 후 배치 통과 |
| `Toast a slice of the breadloaf` | `PASS` | 상태 변화 task 통과 |
| `Slice apple and throw it in the trash` | `INFEASIBLE` | Knife handoff와 action skill 불일치 문제 |

특히 `Put two vegetables in the fridge parallely_plans_06-17-2026-02-14-57`는 실제 실행까지 성공적으로 확인되었다. 이 작업은 하나의 물체를 여러 로봇이 이어받는 handoff 작업이 아니라, Potato와 Lettuce라는 서로 다른 물체를 병렬로 처리하는 작업이다. 따라서 현재 helper set에서도 다중 로봇 협업이 의미 있게 나타난다.

반대로 `Slice apple and throw it in the trash`는 Knife를 든 로봇과 이후 `PutObject`를 해야 하는 로봇이 달라지는 문제가 남았다.

```text
robot2 tries to PutObject Knife, but is holding nothing.
robot1 holds Knife, but lacks PutObject.
```

현재 helper set에는 `TransferObject`가 없기 때문에, 한 로봇이 들고 있는 Knife를 다른 로봇에게 넘겨서 후속 action을 수행하는 plan은 실행할 수 없다.

적용 의미:

> 현재 구조에서 다중 로봇 협업이 잘 작동하는 경우는 서로 다른 객체나 독립 하위 목표를 병렬로 처리할 때다. 하나의 물체나 도구를 로봇 간에 넘겨야 하는 handoff 기반 협업은 여전히 helper 설계가 필요하다.

## 4. 보완 후 추가한 프롬프트 정리

보완 과정에서 추가한 프롬프트는 크게 세 종류다. 하나는 allocation reasoning 단계의 실행 가능성 제약이고, 다른 하나는 최초 code plan 생성을 안정화하기 위한 제약이며, 마지막은 validator 실패 후 code plan을 재생성하기 위한 repair 제약이다.

### 4.0 allocation reasoning 프롬프트

`scripts/run_llm.py`의 allocation 단계에 다음 제약을 추가했다.

```text
There is no TransferObject helper in this codebase.
Do not form a robot team only by taking the union of skills if execution would require one robot to hand a held object or tool to another robot.
If subtasks form a dependent sequence through the same held object or tool, assign the whole dependent sequence to one capable robot whenever such a robot exists.
Use multiple robots for independent subtasks or parallel work that does not require unsupported object handoff.
If no allocation can execute without unsupported object handoff, state that the allocation is infeasible under the current helper set.
```

이 프롬프트의 목적은 다음과 같다.

- skill 합집합만 보고 실행 불가능한 team allocation을 만드는 문제 감소
- `Slice tomato`처럼 tool-dependent sequence를 한 로봇에게 배정하도록 유도
- `Put two vegetables in the fridge parallely`처럼 독립 물체 작업은 다중 로봇 분배 허용
- 현재 helper set에서 불가능한 handoff 기반 협업은 infeasible로 분류 유도

### 4.1 최초 code generation 프롬프트

`scripts/run_llm.py`의 code generation 단계에 다음 제약을 추가했다.

```text
Follow the TASK ALLOCATION when it is directly executable under the helper and object ownership constraints.
If the allocation splits a dependent sequence across robots and there is no TransferObject helper, consolidate that sequence onto one capable robot.
Use only these implemented helper signatures: GoToObject(robot, object), PickupObject(robot, object), PutObject(robot, object, receptacleObject), ThrowObject(robot, object), SliceObject(robot, object), CleanObject(robot, object), OpenObject(robot, object), CloseObject(robot, object), SwitchOn(robot, object), SwitchOff(robot, object), BreakObject(robot, object).
Do not use DropHandObject, PushObject, or PullObject because they are not implemented helper functions in data/aithor_connect/aithor_connect.py.
Never add a target receptacle argument to ThrowObject. Correct: ThrowObject(robot, object). Incorrect: ThrowObject(robot, object, receptacle).
Preserve object ownership: if a robot picks up an object, only that same robot may later PutObject or ThrowObject that held object. There is no TransferObject helper.
Do not add PickupObject unless the object must be moved, placed into a receptacle, or explicitly held for the task.
```

이 프롬프트의 목적은 다음과 같다.

- allocation reasoning과 code plan 불일치 감소
- 실행 불가능한 allocation을 무리하게 보존하는 문제 완화
- 미구현 helper 사용 방지
- helper signature 오류 방지
- object ownership 오류 감소
- 불필요한 `PickupObject` 생성 억제

### 4.2 few-shot code prompt 상단 규칙

`data/pythonic_plans/train_task_allocation_code.py` 상단에도 같은 방향의 code generation rule을 추가했다.

```text
The CODE Solution should follow the robot assignment described in TASK ALLOCATION when it is directly executable.
If the allocation splits a dependent sequence across robots and there is no TransferObject helper, consolidate that sequence onto one capable robot.
Use only the helper signatures below. Never add extra arguments.
Do not use DropHandObject, PushObject, or PullObject.

Object ownership rule:
If a robot picks up an object, only that same robot may later PutObject or ThrowObject that held object.
Do not add PickupObject unless the object must be moved, placed into a receptacle, or explicitly held for the task.
Do not make robot A pick up an object and robot B put or throw that object unless a transfer helper is explicitly available.
Keep tool/object dependent manipulation sequences on the same robot unless a transfer helper is explicitly available.
There is no TransferObject helper in this codebase.
```

이 규칙은 LLM이 few-shot 예제의 스타일을 따라 code plan을 만들 때, helper signature와 ownership 제약을 함께 학습하도록 하기 위한 것이다.

### 4.3 repair code generation 프롬프트

validator가 `REPAIRABLE_PLAN_ERROR`를 반환하면, `run_llm.py`는 기존 `code_plan.py`를 바로 실행하지 않고 repair prompt를 구성해 code plan만 다시 생성한다.

repair prompt에는 다음 입력이 포함된다.

- task description
- ground truth goal
- available robots
- decomposed plan
- allocated plan
- invalid code plan
- validation feedback

repair 단계에는 다음 제약을 추가했다.

```text
Generate a corrected code plan for the same task.
Prefer a valid executable plan over preserving an invalid robot assignment.
Do not copy helper calls from the decomposition if they omit robot arguments.
Every helper call must include a robot argument first.
Correct examples: GoToObject(robot_list[0], 'Lettuce'), PickupObject(robot_list[0], 'Lettuce'), PutObject(robot_list[0], 'Lettuce', 'CounterTop').
Incorrect examples: GoToObject('Lettuce'), PickupObject('Lettuce'), PutObject('Lettuce', 'CounterTop').
Define task functions with robot_list or robot parameters, then call them with robots[index] or [robots[index], ...].
Use the minimum number of robots necessary.
If a subtask sequence depends on the same held object or tool and there is no TransferObject helper, consolidate that sequence onto one capable robot.
Keep object manipulation on the robot currently holding the object.
Do not split PickupObject and PutObject/ThrowObject across robots unless TransferObject exists.
TransferObject does not exist in this codebase.
If validation says a PickupObject is unnecessary for the final goal, remove that PickupObject instead of putting down the currently held object.
If the goal says a receptacle contains an object, prefer PutObject(robot, object, receptacleObject) over ThrowObject.
Use only implemented helper signatures exactly as provided.
Return only corrected executable Python code.
```

이 프롬프트의 목적은 다음과 같다.

- decomposed plan의 robot 없는 pseudo code를 그대로 복사하는 문제 방지
- validator가 찾은 실패 원인을 반영한 code plan 재생성
- state goal에서 불필요한 `PickupObject`를 제거하도록 유도
- containment goal에서 `ThrowObject` 대신 `PutObject` 대체 유도
- repair 이후에도 helper signature를 지키도록 강제

### 4.4 프롬프트 수정에서 피한 방향

이번 보완에서는 `SliceObject` 같은 특정 action 전용 규칙을 직접 추가하지 않았다. 대신 다음과 같은 일반 원칙으로 처리했다.

```text
Do not add PickupObject unless the object must be moved, placed into a receptacle, or explicitly held for the task.
```

이렇게 한 이유는 특정 task/action마다 예외 규칙을 늘리면 프롬프트가 빠르게 복잡해지고, 다른 작업에서 또 다른 부작용을 만들 수 있기 때문이다. 따라서 현재 방향은 행동별 hard-coded rule보다 ground truth goal, helper signature, object ownership을 기준으로 일반화된 제약을 주는 것이다.

## 5. 남은 보완 방향

현재 적용된 보완은 실행 전 정적 검증과 repairable plan의 최대 3회 재생성까지다. 아직 남은 보완은 다음과 같다.

1. task-level success 자동 평가 추가
2. repair 재시도 이후에도 실패한 plan의 원인별 통계 저장
3. random seed와 초기 위치 로그 저장
4. 실제 다중 로봇 handoff를 다루려면 `TransferObject` 또는 이에 준하는 helper 설계
5. repair prompt가 allocated plan을 얼마나 변경했는지 비교하는 consistency report 추가
6. repair 이후에도 helper signature 오류가 남으면 원본 plan과 repaired plan을 모두 보존하는 versioned log 추가
7. `SliceObject`, `CleanObject` 같은 action별 precondition/effect schema 추가
