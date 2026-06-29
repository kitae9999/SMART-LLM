# SMART-LLM 발표자료용 분석 문서

## 1. 발표 목표

이 발표의 목표는 프로젝트 페이지에 소개된 SMART-LLM 코드를 단순히 소개하는 것이 아니라, 로컬 레포의 코드를 직접 실행 가능한 형태로 이해하고, AI2-THOR/iTHOR 시뮬레이터에서 다중 로봇 작업 계획이 어떤 방식으로 생성되고 수행되는지 분석한 뒤, 보완해야 할 지점을 정리하는 것이다.

발표에서 먼저 다룰 세 가지 축은 다음과 같다.

1. SMART-LLM은 어떤 프로젝트인가
2. iTHOR(AI2-THOR)는 이 프로젝트에서 어떤 검증 환경인가
3. 현재 레포는 원 논문의 아이디어를 어떤 코드 구조로 구현하고 있으며, 내가 어떻게 해석했는가

후반부에서는 현재 코드로 어떤 테스트를 수행할지, 각 테스트가 어떤 의미를 갖는지 계획으로 제시한다.

## 2. 핵심 발표 메시지

SMART-LLM은 고수준 자연어 명령을 다중 로봇이 실행 가능한 작업 계획으로 변환하는 프레임워크다. 핵심은 LLM이 단순히 문장을 생성하는 것이 아니라, 로봇의 스킬, 환경 내 객체, 작업 순서, 병렬 가능성, 로봇별 역량을 고려해 Python 형태의 실행 계획을 생성한다는 점이다.

내 해석은 다음과 같다.

SMART-LLM은 "로봇 제어기를 처음부터 학습하는 시스템"이 아니라, 이미 정의된 로봇 스킬 API 위에서 LLM을 고수준 계획자(planner)로 사용하는 시스템이다. 즉, 저수준 이동/조작은 AI2-THOR API와 보조 함수가 담당하고, LLM은 작업을 분해하고 어떤 로봇 또는 로봇 팀이 어떤 순서로 수행할지 결정한다.

따라서 이 레포의 역할은 크게 두 가지다.

1. 자연어 작업 지시를 프롬프트 기반으로 분해, 할당, 코드화하는 LLM 계획 생성기
2. 생성된 Python 계획을 AI2-THOR 다중 에이전트 환경에서 실행하고 결과를 영상 및 지표로 검증하는 실행 파이프라인

## 3. SMART-LLM 프로젝트 개요

SMART-LLM의 전체 이름은 "Smart Multi-Agent Robot Task Planning using Large Language Models"다. 프로젝트 페이지와 논문은 SMART-LLM을 embodied multi-robot task planning 프레임워크로 설명한다. 여기서 embodied는 로봇이 추상적인 텍스트 계획만 만드는 것이 아니라, 객체와 상태가 있는 물리 기반 시뮬레이션 또는 실제 로봇 환경에서 실행 가능한 계획을 다룬다는 의미다.

원 프로젝트가 제시하는 주요 단계는 다음과 같다.

| 단계 | 역할 | 이 레포에서의 대응 |
| --- | --- | --- |
| Task Decomposition | 고수준 명령을 하위 작업으로 분해 | `data/pythonic_plans/train_task_decompose.py`, `scripts/run_llm.py` |
| Coalition Formation | 필요한 로봇 조합 또는 팀 구성을 판단 | allocation prompt의 reasoning 영역 |
| Task Allocation | 각 하위 작업을 로봇 또는 로봇 팀에 할당 | `data/pythonic_plans/train_task_allocation_solution.py` |
| Task Execution | 생성된 계획 코드를 환경에서 실행 | `scripts/execute_plan.py`, `data/aithor_connect/aithor_connect.py` |

원 논문은 작업 난이도를 elemental, simple, compound, complex로 나누어 평가한다. 이 구분은 발표에서 "왜 여러 유형의 테스트가 필요한가"를 설명하는 데 유용하다.

- Elemental task: 하나의 단순 조작 중심 작업
- Simple task: 여러 객체가 포함되지만 로봇 스킬이 충분한 비교적 단순한 작업
- Compound task: 병렬/순차 실행 판단과 이질적 로봇 스킬 고려가 필요한 작업
- Complex task: 단일 로봇으로는 수행하기 어려워 팀 구성과 조합적 추론이 필요한 작업

## 4. iTHOR(AI2-THOR)의 역할

iTHOR는 AI2-THOR의 실내 가정 환경 시뮬레이션 영역이다. AI2-THOR는 Unity 기반의 물리 시뮬레이션을 제공하며, 부엌, 거실, 침실, 욕실 같은 실내 장면에서 객체를 탐색하고 조작할 수 있다.

이 프로젝트에서 iTHOR가 중요한 이유는 다음과 같다.

1. 객체 상태가 존재한다.
   예를 들어 Fridge는 열고 닫을 수 있고, Microwave는 켜고 끌 수 있으며, 일부 객체는 Hot, Cold, Cooked, Sliced, Broken 같은 상태를 가진다.

2. 로봇 행동이 명시적 API로 표현된다.
   `GoToObject`, `PickupObject`, `PutObject`, `OpenObject`, `CloseObject`, `SliceObject`, `SwitchOn`, `SwitchOff`, `ThrowObject` 같은 행동이 코드의 기본 단위가 된다.

3. 다중 에이전트 실행이 가능하다.
   AI2-THOR는 `agentCount`로 하나의 scene 안에 여러 agent를 초기화할 수 있다. 이 레포도 `agentCount=no_robot` 형태로 여러 로봇을 같은 FloorPlan 안에 배치한다.

4. 평가 가능한 목표 상태가 있다.
   실행 후 metadata에서 객체의 `isOpen`, `isToggled`, `isSliced`, `isCooked`, `temperature`, `receptacleObjectIds` 등을 확인하여 목표 달성 여부를 계산한다.

발표에서는 iTHOR를 "LLM이 만든 계획이 실제 환경 제약을 만족하는지 확인하는 검증 무대"로 설명하는 것이 적절하다. 텍스트로 보기에는 그럴듯한 계획도, 실제 시뮬레이터에서는 객체가 없거나, 로봇 스킬이 부족하거나, 순서가 틀리면 실패한다. 이 차이가 SMART-LLM 실험의 핵심이다.

## 5. 로컬 레포 구조 분석

현재 레포의 주요 구조는 다음과 같다.

| 경로 | 역할 |
| --- | --- |
| `README.md` | 원 프로젝트 설명, 설치 방법, 실행 명령 |
| `requirments.txt` | Python 의존성 목록. 파일명은 원본 그대로 `requirements`가 아니라 `requirments`다 |
| `resources/actions.py` | LLM 프롬프트에 제공할 AI2-THOR 액션 목록 |
| `resources/robots.py` | 로봇별 스킬과 질량 처리 능력 정의 |
| `data/pythonic_plans/` | 작업 분해, 할당 reasoning, 실행 코드 생성을 위한 few-shot 예시 |
| `data/final_test/` | FloorPlan별 테스트 작업, 사용 로봇, 목표 객체 상태 |
| `scripts/run_llm.py` | 작업 데이터와 환경 객체 정보를 읽고 LLM으로 계획을 생성 |
| `scripts/execute_plan.py` | 생성된 계획과 실행 커넥터를 합쳐 실행 파일 생성 후 실행 |
| `data/aithor_connect/` | AI2-THOR controller 초기화, 다중 agent 실행, 액션 큐, 평가, 영상 생성 |
| `logs/` | LLM 생성 결과, 실행 스크립트, agent/top-view 이미지와 비디오 |

## 6. 실행 흐름

전체 실행 흐름은 다음과 같이 정리할 수 있다.

```text
data/final_test/FloorPlanX.json
        |
        v
scripts/run_llm.py
        |
        |-- AI2-THOR scene에서 객체 목록과 mass 추출
        |-- resources/actions.py의 가능한 action 목록 삽입
        |-- resources/robots.py에서 task별 robot list 구성
        |-- few-shot prompt로 task decomposition 생성
        |-- allocation prompt로 robot/task 할당 reasoning 생성
        |-- code prompt로 실행 가능한 Python code 생성
        v
logs/{task}_plans_{timestamp}/
        |-- log.txt
        |-- decomposed_plan.py
        |-- allocated_plan.py
        |-- code_plan.py
        |
        v
scripts/execute_plan.py
        |
        |-- imports_aux_fn.py
        |-- log.txt에서 robots, floor_no, ground_truth 추출
        |-- aithor_connect.py
        |-- code_plan.py
        |-- end_thread.py
        v
logs/{task}/executable_plan.py 실행
        |
        |-- AI2-THOR 다중 agent 실행
        |-- agent별 이미지 저장
        |-- top view 이미지 저장
        |-- video_agent_N.mp4, video_top_view.mp4 생성
        |-- SR, TC, GCR, Exec, RU 계산
```

실행 명령은 README 기준으로 다음과 같다.

```bash
python3 scripts/run_llm.py --floor-plan 6
python3 scripts/execute_plan.py --command Slice_the_tomato_plans_04-24-2026-21-20-11
```

발표에서는 이 흐름을 "생성 단계"와 "실행 단계"로 나누어 보여주면 이해가 쉽다.

## 7. 내가 이 레포를 해석한 방식

이 레포는 SMART-LLM 논문의 모든 실험을 완전 자동 재현하는 거대한 프레임워크라기보다는, 논문 아이디어를 AI2-THOR에서 확인할 수 있게 만든 연구 코드에 가깝다.

핵심 해석은 다음과 같다.

1. LLM은 직접 AI2-THOR를 조작하지 않는다.
   LLM은 Python 함수 호출 형태의 계획을 생성한다. 실제 조작은 `aithor_connect.py`에 정의된 helper 함수와 AI2-THOR controller가 수행한다.

2. 로봇 능력은 symbolic하게 주어진다.
   `resources/robots.py`의 로봇은 실제 로봇 모델이라기보다 스킬 목록과 mass capacity를 가진 계획 단위다. LLM은 이 symbolic capability를 보고 어떤 로봇이 어떤 작업을 맡을지 결정한다.

3. 환경은 metadata로 grounding된다.
   `run_llm.py`는 AI2-THOR scene을 열어 객체 종류와 질량 정보를 가져오고, 이를 프롬프트에 넣는다. 즉, 계획은 현재 FloorPlan의 객체 목록을 바탕으로 생성된다.

4. 병렬 실행은 Python threading으로 표현된다.
   LLM이 병렬 가능한 작업을 판단하면 thread를 생성하는 코드가 나올 수 있다. 실제 실행은 action queue를 통해 controller step으로 흘러간다.

5. 평가 기준은 객체 최종 상태다.
   단순히 코드가 에러 없이 실행됐는지가 아니라, 목표 객체 상태가 달성됐는지 확인한다. 예를 들어 Tomato가 sliced 되었는지, Fridge가 Apple을 contain하는지, LightSwitch가 off인지 확인한다.

## 8. 현재 로컬 실행 결과로 확인된 내용

현재 `logs/`에는 FloorPlan6 기준으로 생성 및 실행된 3개의 작업 결과가 있다. 모두 `gpt-4o-mini`로 계획이 생성된 기록을 포함한다.

| 작업 | FloorPlan | 로봇 수 | 목표 상태 | 산출물 |
| --- | --- | ---: | --- | --- |
| Slice the tomato | 6 | 3 | Tomato가 SLICED | `decomposed_plan.py`, `allocated_plan.py`, `code_plan.py`, `executable_plan.py`, agent/top-view video |
| Wash the lettuce and place lettuce on the Countertop | 6 | 3 | CounterTop이 Lettuce를 포함 | 동일 |
| Throw the Spatula in the trash | 6 | 2 | GarbageCan이 Spatula를 포함 | 동일 |

각 로그 폴더에는 다음 산출물이 있다.

- `log.txt`: 작업명, GPT 버전, FloorPlan, 객체 목록, 로봇 목록, ground truth
- `decomposed_plan.py`: LLM이 생성한 작업 분해 결과
- `allocated_plan.py`: 로봇 할당 reasoning 결과
- `code_plan.py`: 실행 가능한 Python 함수 호출 계획
- `executable_plan.py`: AI2-THOR 연결 코드와 생성 계획을 합친 실제 실행 파일
- `agent_*/img_*.png`: 로봇별 관측 이미지
- `top_view/img_*.png`: top-view 이미지
- `video_agent_*.mp4`, `video_top_view.mp4`: 실행 장면 영상

예를 들어 `Slice the tomato` 작업의 최종 실행 코드는 다음처럼 단일 로봇에게 knife pickup, tomato 이동, slice를 맡기는 형태로 생성되어 있다.

```python
def slice_tomato(robot_list):
    GoToObject(robot_list[0], 'Knife')
    PickupObject(robot_list[0], 'Knife')
    GoToObject(robot_list[0], 'Tomato')
    SliceObject(robot_list[0], 'Tomato')

slice_tomato([robots[0]])
```

이 예시는 발표에서 "LLM이 자연어를 실행 가능한 API 호출로 바꾸는 과정"을 보여주기에 좋다. 다만 분해 결과에는 설명과 Markdown 코드블록이 섞여 있었고, 실행 코드는 그중 Python 코드만 추출해 저장하는 방식이 필요했다.

### 8.1 FloorPlan6 2회 테스트 결과

FloorPlan6의 세 지침에 대해 두 차례 계획 생성 및 실행 테스트를 수행했다. 비교 대상은 `04-24-2026-21-20-11` 로그 세트와 `05-03-2026-16-37-09` 로그 세트다. 두 세트 모두 같은 FloorPlan6와 같은 세 작업을 대상으로 하지만, LLM이 생성한 `allocated_plan.py`와 `code_plan.py`는 완전히 동일하지 않았다.

이 결과는 발표에서 중요한 포인트가 된다. SMART-LLM의 출력은 deterministic한 컴파일 결과가 아니라 LLM 생성 결과이므로, 같은 작업이라도 세부 작업 분해, 로봇 선택, 보조 동작 포함 여부, API 호출 형태가 달라질 수 있다.

| 작업 | 1차 결과: `04-24-2026-21-20-11` | 2차 결과: `05-03-2026-16-37-09` | 발표용 해석 |
| --- | --- | --- | --- |
| Slice the tomato | `robots[0]` 하나가 Knife로 이동, Knife 집기, Tomato로 이동, Tomato 자르기 수행 | `robots[0]` 하나가 동일 작업을 수행하고 마지막에 Knife를 CounterTop에 되돌려 놓는 동작 추가 | 단순 상태 변경 작업은 두 번 모두 단일 로봇 계획으로 안정적으로 생성됨. 다만 2차에서는 목표 조건에는 없는 정리 동작이 추가됨 |
| Wash the lettuce and place lettuce on the Countertop | `robots[0]` 하나가 Lettuce 집기, Sink 이동, Faucet on/off, Lettuce 다시 집기, CounterTop에 놓기를 모두 수행 | Wash는 Robot 1, place는 Robot 2로 분리됨. 하지만 Robot 1이 Lettuce를 들고 있는데 Robot 2가 CounterTop에 놓으려는 형태라 handoff가 없음 | 병렬/분담 계획처럼 보이지만 물체 소유권이 이어지지 않음. 현재 executor는 로봇 간 handoff 검증이 약함 |
| Throw the Spatula in the trash | `allocated_plan.py`는 Robot 1+2 팀이 필요하다고 판단했지만, `code_plan.py`는 `robots[1]` 한 대가 Spatula를 집고 던지는 코드 생성 | Robot 2가 Spatula를 집고 Robot 1이 던지는 팀 계획을 생성했지만, `ThrowObject(robot, object, target)`처럼 3개 인자를 넘겨 실행 시 TypeError 발생 | allocation reasoning과 code plan이 어긋날 수 있음. 또한 action API signature 검증이 없어 실행 단계에서 오류가 드러남 |

작업별 핵심 관찰은 다음과 같다.

1. `Slice the tomato`는 두 차례 모두 가장 안정적이었다.
   두 결과 모두 한 로봇이 전체 작업을 수행한다. 목표 조건도 `Tomato`의 `SLICED` 상태 하나뿐이므로, 계획과 평가 조건이 단순하다. 차이는 2차 결과에 `PutObject(robot_list[0], 'Knife', 'CounterTop')`가 추가되어 후처리 동작이 더 들어갔다는 점이다.

2. `Wash the lettuce and place lettuce on the Countertop`는 1차가 더 실행 논리에 맞았다.
   1차는 한 로봇이 Lettuce를 계속 소유한 상태로 세척과 배치를 수행하므로 AI2-THOR의 object-in-hand 모델과 잘 맞는다. 반면 2차는 세척 로봇과 배치 로봇을 나누었지만, Lettuce를 Robot 1에서 Robot 2로 넘기는 handoff 동작이 없다. 이 결과는 "다중 로봇으로 작업을 나누는 reasoning"과 "실제 시뮬레이터에서 가능한 물체 전달" 사이에 간극이 있음을 보여준다.

3. `Throw the Spatula in the trash`는 가장 중요한 한계 사례다.
   두 차례 모두 allocation reasoning은 Robot 1과 Robot 2의 스킬이 상호 보완적이라고 판단했다. Robot 1은 `ThrowObject`가 있지만 `PickupObject`가 없고, Robot 2는 `PickupObject`가 있지만 `ThrowObject`가 없다. 따라서 reasoning상으로는 팀 구성이 필요하다. 그러나 1차 code plan은 Robot 2 혼자 `ThrowObject`까지 호출했고, 2차 code plan은 Robot 2가 집고 Robot 1이 던지는 형태에 더해 `ThrowObject` 인자 개수까지 helper 정의와 맞지 않았다.

이 결과의 의미는 다음과 같이 정리할 수 있다.

| 관찰 | 의미 |
| --- | --- |
| 같은 작업도 두 번 실행하면 code plan이 달라짐 | LLM 기반 planner는 출력 변동성이 있으므로 단일 실행만으로 성능을 단정하기 어렵다 |
| allocation reasoning과 code plan이 불일치함 | reasoning을 입력으로 쓰지만, reasoning 결과가 구조화된 제약으로 강제되지는 않는다 |
| robot skill 위반이 실행 전에 잡히지 않음 | executor가 `robot['skills']`를 검사하지 않으므로 스킬이 없는 로봇도 action 호출 가능 |
| handoff 없이 다른 로봇이 물체를 조작하려 함 | 다중 로봇 작업에서 object ownership/handoff 검증이 필요하다 |
| helper API와 다른 인자 개수를 생성함 | `GoToObject`, `PickupObject`, `ThrowObject` 등 action signature validator가 필요하다 |

발표에서는 이 부분을 단순 실패 사례가 아니라, "연구 코드 실행을 통해 발견한 SMART-LLM 구현상의 보완 지점"으로 제시하는 것이 좋다. 즉, SMART-LLM의 아이디어는 자연어에서 다중 로봇 계획을 만드는 데 의미가 있지만, 현재 레포 수준에서는 LLM이 만든 중간 reasoning과 최종 실행 코드 사이에 검증 레이어가 부족하다.

## 9. 현재 작업본에서 확인되는 보완 포인트

현재 로컬 작업본에는 원본 대비 실행 안정성을 높이는 방향의 변경이 들어가 있다.

| 영역 | 보완 내용 | 의미 |
| --- | --- | --- |
| OpenAI SDK | `openai==0.28.1`로 고정 | 기존 `openai.ChatCompletion.create` 방식과 호환 |
| 모델 선택 | `gpt-4o-mini`, `gpt-4o`, `gpt-4.1-mini` 선택지 추가 | 비용이 낮거나 최신 계열 모델로 실험 가능 |
| 코드 추출 | Markdown 코드블록에서 Python 코드만 추출 | LLM이 설명문을 함께 반환해도 실행 파일 오염 감소 |
| 실행 Python | `sys.executable` 사용 | 현재 가상환경의 Python으로 실행 |
| subprocess | `check=True` 사용 | 실행 실패를 조용히 무시하지 않고 에러로 드러냄 |
| OpenCV 창 | `SMART_LLM_SHOW_CV2=1`일 때만 창 표시 | headless 환경에서도 실행 가능 |

발표에서는 이 부분을 "논문 코드 실행 과정에서 필요한 실용적 보완"으로 설명할 수 있다. 연구 코드는 특정 환경에서는 동작하지만, 로컬/서버/headless 환경에서는 SDK 버전, GUI 창, LLM 응답 형식 차이 때문에 쉽게 깨질 수 있다.

## 10. 추가로 보완하면 좋은 지점

실제 발표 또는 추가 실험 전에 다음 보완을 하면 결과의 신뢰도가 올라간다.

| 우선순위 | 보완 항목 | 이유 |
| --- | --- | --- |
| 높음 | `data/final_test` 데이터 정규화 | 여러 파일에 Python식 `None`, 누락된 괄호, 누락 필드가 있어 `json.loads`가 실패할 수 있음 |
| 높음 | 실행 결과 지표를 파일로 저장 | 현재 SR, TC, GCR, Exec, RU는 출력만 되고 별도 결과 파일로 남지 않음 |
| 높음 | 단일 task 실행 옵션 추가 | FloorPlan 전체를 매번 돌리는 대신 특정 작업만 반복 검증 가능 |
| 중간 | 생성 코드 syntax validation | LLM이 만든 code_plan을 실행 전에 `compile()`로 검증 |
| 중간 | action coverage 보완 | action 목록에는 `CleanObject`, `PushObject`, `PullObject`, `DropHandObject`가 있으나 실행 처리와 helper coverage가 제한적 |
| 중간 | seed 고정 | agent 초기 위치 randomization 때문에 실행 결과 재현성이 낮아질 수 있음 |
| 중간 | object 선택 로직 개선 | 같은 object type이 여러 개 있을 때 첫 번째 match를 고르는 방식은 목표와 다른 객체를 선택할 수 있음 |
| 낮음 | log parsing 구조화 | `execute_plan.py`가 `log_data[8]` 같은 고정 line index에 의존함 |

## 11. 테스트 수행 계획

테스트는 "코드가 실행되는가"만 보는 것이 아니라, SMART-LLM이 주장하는 다중 로봇 계획 능력을 단계적으로 확인하도록 구성한다.

### 11.1 0단계: 환경 및 데이터 검증

목표는 본격적인 로봇 작업 실행 전에 실험이 실패할 구조적 원인을 제거하는 것이다.

| 테스트 | 수행 내용 | 의미 |
| --- | --- | --- |
| dependency check | `pip install -r requirments.txt`, Python 3.9 계열 확인 | 원 코드 실행 환경 확보 |
| AI2-THOR smoke test | Controller가 FloorPlan6을 열고 객체 metadata를 읽는지 확인 | 시뮬레이터가 정상 구동되는지 확인 |
| dataset parse test | `data/final_test/*.json` 각 line이 파싱되는지 확인 | LLM 호출 전에 데이터 오류를 분리 |
| API key check | `api_key.txt` 존재 여부만 확인하고 내용은 출력하지 않음 | LLM 호출 가능성 확인 |

이 단계의 의미는 발표에서 "실험 실패가 LLM의 계획 실패인지, 코드/데이터/환경 문제인지 분리했다"라고 설명할 수 있다는 점이다.

### 11.2 1단계: FloorPlan6 기본 재현 테스트

현재 로그가 존재하는 FloorPlan6을 우선 재현한다.

| 작업 | 검증 포인트 | 의미 |
| --- | --- | --- |
| Slice the tomato | `SliceObject`가 호출되고 Tomato `isSliced=True`가 되는지 | elemental task 재현 |
| Wash the lettuce and place lettuce on the Countertop | pickup, sink/faucet, put object 흐름이 생성되는지 | 조작 순서와 목표 receptacle 검증 |
| Throw the Spatula in the trash | pickup 담당 로봇과 throw 담당 로봇이 분리되는지 | 이질적 스킬 조합과 팀 과제 검증 |

이 테스트는 발표에서 "가장 작은 단위로 SMART-LLM의 계획 생성과 실행 파이프라인을 끝까지 연결했다"는 증거가 된다.

### 11.3 2단계: 단일 객체 상태 변경 테스트

단일 객체 상태 변화는 가장 명확한 성공/실패 기준을 제공한다.

| 후보 작업 | FloorPlan | 목표 상태 | 의미 |
| --- | --- | --- | --- |
| Make the kitchen dark | 15 | LightSwitch OFF | toggle action 검증 |
| Turn on the laptop | 303 | Laptop ON | toggle action과 object match 검증 |
| Turn off floor lamp | 209 | FloorLamp OFF | 누락된 ground truth 보완 필요성을 보여주는 사례 |

이 단계는 "LLM 계획 생성 이전에 환경 action이 제대로 작동하는가"를 확인하는 기본 단위다.

### 11.4 3단계: 순차 작업 테스트

순차 작업은 앞선 행동의 결과가 다음 행동의 전제가 되는 경우다.

| 후보 작업 | 검증 포인트 | 의미 |
| --- | --- | --- |
| Put mug in coffee machine | mug pickup 후 CoffeeMachine에 put | receptacle 관계 검증 |
| Cook the potato and put it in the Fridge | microwave/stove 계열 가열 후 fridge 이동 | 상태 변화와 이동 순서 검증 |
| Open the Laptop and Turn it ON | OpenObject 후 SwitchOn | 순서 제약 검증 |

이 테스트의 의미는 LLM이 단순히 필요한 action 목록을 나열하는 것이 아니라, open -> put -> close -> switch 같은 선후관계를 지키는지 확인하는 것이다.

### 11.5 4단계: 병렬 작업 테스트

SMART-LLM의 장점은 여러 로봇이 병렬로 할 수 있는 일을 분리한다는 점이다.

| 후보 작업 | 병렬성 포인트 | 의미 |
| --- | --- | --- |
| Put apple in fridge and switch off the light | apple 이동과 light off는 독립 가능 | 독립 subtask 분해 검증 |
| Chill the apple and wash the knife | 냉각 작업과 세척 작업 병렬 가능 | 다중 객체 병렬 처리 검증 |
| Put two vegetables in the fridge parallely | Potato와 Lettuce를 다른 로봇이 병렬 처리 가능 | 동일 receptacle을 공유하는 병렬 작업 검증 |

이 테스트는 발표에서 "다중 로봇을 쓰는 이유"를 가장 잘 보여준다. 단일 로봇이면 순차 실행해야 하지만, 여러 로봇이면 독립 작업을 동시에 처리할 수 있다.

### 11.6 5단계: 이질적 로봇 스킬 할당 테스트

로봇이 모두 같은 능력을 가진다면 task allocation의 의미가 약해진다. 따라서 서로 다른 skill set을 가진 로봇 조합을 테스트해야 한다.

| 후보 작업 | 필요한 스킬 | 의미 |
| --- | --- | --- |
| Throw the Spatula in the trash | PickupObject, ThrowObject | pickup 전담과 throw 전담이 분리될 수 있는지 |
| Wash the fork and put it in the bowl | PickupObject, PutObject, SwitchOn/Off | specialist robot 조합 필요성 |
| Open a book in a well lit room | OpenObject, SwitchOn | open 담당과 light 담당 분리 |
| Slice apple and throw it in the trash | SliceObject, PickupObject, ThrowObject | 복합 skill 조합 검증 |

이 테스트의 의미는 "LLM이 로봇의 스킬 제약을 읽고, 단일 로봇이 불가능하면 팀 또는 적절한 로봇을 선택하는가"를 확인하는 것이다.

### 11.7 6단계: 질량 capacity 기반 팀 구성 테스트

`resources/robots.py`에는 같은 스킬을 갖지만 mass capacity가 다른 로봇들이 있다. 이를 사용하면 "스킬은 충분하지만 물체를 들 수 있는 capacity가 부족한 상황"을 만들 수 있다.

| 테스트 방향 | 수행 내용 | 의미 |
| --- | --- | --- |
| 단일 로봇 가능 | object mass <= robot capacity | 불필요한 팀을 만들지 않는지 확인 |
| 단일 로봇 불가능 | object mass > 각 robot capacity | 여러 로봇 팀을 구성하는지 확인 |
| 과도한 팀 구성 방지 | minimum number of robots 조건 확인 | 자원 효율성 검증 |

이 단계는 원 논문의 coalition formation 개념과 가장 직접적으로 연결된다. 단순히 "누가 할 수 있는가"가 아니라 "최소 몇 대가 같이 해야 하는가"를 평가한다.

### 11.8 7단계: 실패 및 한계 시나리오 테스트

성공 사례만 보여주면 시스템의 신뢰도를 설명하기 어렵다. 실패를 의도적으로 분리해 보여주는 것이 발표에 더 설득력 있다.

| 실패 유형 | 예시 | 확인할 의미 |
| --- | --- | --- |
| 필요한 skill 없음 | SliceObject 가능한 로봇 없이 slicing 요청 | infeasible task 인식 |
| 객체 없음 | FloorPlan에 없는 객체를 조작하도록 요청 | environment grounding 실패 탐지 |
| malformed dataset | `None`, 괄호 누락, 필드 누락 | 실험 데이터 정제 필요성 |
| ambiguous object | Cabinet, CounterTop처럼 여러 instance 존재 | object selection heuristic 한계 |
| action 미구현 | CleanObject/Push/Pull 등 | action list와 executor 불일치 확인 |

이 단계의 의미는 "LLM 계획 성능"과 "실행 시스템 완성도"를 구분해서 해석할 수 있게 하는 것이다.

## 12. 평가 지표 해석

`end_thread.py`는 실행 후 다음 지표를 계산한다.

| 지표 | 의미 | 발표에서의 해석 |
| --- | --- | --- |
| Exec | 실행 가능한 action 비율 | 코드와 simulator API가 충돌하지 않았는가 |
| GCR | 목표 상태 조건 중 달성한 비율 | 최종 환경 상태가 목표와 얼마나 일치하는가 |
| TC | task completion | 목표 조건 전체를 달성했는가 |
| RU | robot utilization | transition 수 기준으로 효율적인 로봇 사용을 했는가 |
| SR | success rate | TC와 RU를 함께 만족한 최종 성공 여부 |

발표에서는 "Exec은 코드가 돌아갔는가, GCR/TC는 목적을 달성했는가, RU/SR은 다중 로봇을 효율적으로 썼는가"로 설명하면 된다.

## 13. 발표 슬라이드 구성안

1. 제목: SMART-LLM 코드 실행 및 iTHOR 기반 다중 로봇 작업 수행 분석
2. 연구 배경: 자연어 명령에서 다중 로봇 실행 계획으로
3. SMART-LLM 개요: task decomposition, coalition formation, allocation, execution
4. AI2-THOR/iTHOR 소개: 가정 환경, 객체 상태, 다중 agent
5. 로컬 레포 구조: `scripts`, `data`, `resources`, `logs`
6. 실행 파이프라인: `run_llm.py`와 `execute_plan.py`
7. 실제 실행 예시: FloorPlan6의 Slice tomato 또는 Throw spatula
8. 내가 해석한 핵심: LLM planner + symbolic robot skills + simulator executor
9. 보완한/보완할 지점: SDK, 코드 추출, headless 실행, dataset 정규화, metric 저장
10. 테스트 계획: elemental, sequential, parallel, heterogeneous, mass/team, failure case
11. 테스트의 의미: 계획 생성 성능과 실제 실행 가능성의 간극 확인
12. 결론: SMART-LLM은 LLM 기반 계획을 실제 환경 검증으로 연결하는 좋은 출발점이지만, 재현성과 실행 안정성을 위해 데이터/로그/검증 보완이 필요함

## 14. 결론 문장 초안

이번 분석을 통해 SMART-LLM 코드는 LLM이 고수준 명령을 Pythonic multi-robot plan으로 변환하고, AI2-THOR 환경에서 이를 실제 객체 상태 변화로 검증하는 구조임을 확인했다. 내가 수행할 테스트의 핵심은 단순 실행 성공 여부가 아니라, 작업 분해, 로봇 할당, 병렬성 판단, 이질적 스킬 조합, 최종 상태 검증이 각각 제대로 작동하는지를 단계적으로 확인하는 것이다.

따라서 발표의 결론은 "SMART-LLM은 다중 로봇 작업 계획에서 LLM의 추론 능력을 활용하는 실험적 프레임워크이며, 이 레포는 그 아이디어를 AI2-THOR에서 실행 가능한 코드와 로그로 확인할 수 있게 해준다. 다만 연구 코드를 재현 가능한 실험 시스템으로 쓰기 위해서는 데이터 정규화, 실행 지표 저장, action coverage, deterministic execution 같은 보완이 필요하다"로 잡으면 된다.

## 15. 참고 자료

- SMART-LLM 프로젝트 페이지: https://sites.google.com/view/smart-llm/
- SMART-LLM GitHub 레포: https://github.com/SMARTlab-Purdue/SMART-LLM
- SMART-LLM 논문: https://arxiv.org/abs/2309.10062
- AI2-THOR 공식 사이트: https://ai2thor.allenai.org/
- AI2-THOR multi-agent example: https://allenai.github.io/ai2thor-v2.1.0-documentation/examples
- AI2-THOR object types and states: https://ai2thor.allenai.org/ithor/documentation/objects/object-types/
