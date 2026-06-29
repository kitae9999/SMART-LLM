# SMART-LLM 최종발표 스크립트

## 1번 슬라이드

SMART-LLM 코드베이스를 직접 실행하면서 확인한 한계와, 이를 보완한 결과를 중심으로 발표하겠습니다.

이번 발표는 프로젝트 소개보다는 실제 실행에서 어떤 문제가 발생했고, validator와 feedback loop로 어떻게 줄였는지에 초점을 두었습니다.

## 2번 슬라이드

먼저 기존 코드베이스를 실행하면서 확인한 핵심 한계입니다.

첫 번째는 LLM에게 제공되는 action 목록과 실제 executor 구현이 일치하지 않는 문제였습니다. `DropHandObject`처럼 action 목록에는 있지만 실제 helper에는 없는 함수가 있었고, 이런 경우 실행 단계에서 `NameError`가 발생했습니다.

두 번째는 helper signature 오류입니다. 예를 들어 `ThrowObject`는 실제로 `robot, object` 두 인자를 받는데, LLM이 `GarbageCan` 같은 target을 세 번째 인자로 추가하는 코드가 생성되었습니다.

세 번째는 object ownership 문제입니다. 어떤 로봇이 물체를 들었는지와 이후 `PutObject`나 `ThrowObject`를 수행하는 로봇이 일치하지 않는 경우가 있었습니다.

즉, LLM 출력이 Python 코드처럼 보여도 실제 AI2-THOR executor 제약을 만족한다는 보장은 없었습니다.

## 3번 슬라이드

한계가 확인된 뒤 보완한 실행 구조는 이렇게 정리할 수 있습니다.

기존 구조에서는 LLM이 만든 code plan을 거의 바로 AI2-THOR 실행으로 넘겼습니다. 그래서 helper가 없거나 signature가 틀리거나 손 상태가 맞지 않는 문제가 실행 중에 터졌습니다.

보완 후에는 code plan과 실제 실행 사이에 validator를 넣었습니다.

validator는 code plan을 실행하기 전에 검사하고, 결과를 `PASS`, `REPAIRABLE`, `INFEASIBLE`로 나눕니다.

`PASS`면 실행 후보로 두고, `REPAIRABLE`이면 validation feedback을 이용해 code plan을 다시 생성합니다.

`INFEASIBLE`이면 현재 helper나 robot skill 조건으로는 실행할 수 없다고 보고 실행 전에 차단합니다.

이 구조를 먼저 이해하면, 다음 슬라이드에서 validator가 어떤 입력을 받고 무엇을 검사하는지 자연스럽게 이어집니다.

## 4번 슬라이드

이제부터는 validator를 중심으로 설명하겠습니다.

validator는 LLM이 만든 `code_plan.py`를 바로 실행하지 않고, 실행 전에 검사하는 계층입니다.

입력으로는 세 가지가 들어갑니다. 첫 번째는 LLM이 만든 `code_plan.py`, 두 번째는 robot과 skill 정보, 세 번째는 ground truth 목표입니다.

validator는 helper가 실제 구현되어 있는지, signature가 맞는지, 해당 robot이 그 skill을 갖고 있는지, 그리고 object ownership이 맞는지를 검사합니다.

또 helper 호출 순서를 따라가며 robot hand state를 추적합니다.

출력은 `PASS`, `REPAIRABLE`, `INFEASIBLE`로 나눕니다.

`PASS`는 그대로 실행 후보 plan이고, `REPAIRABLE`은 작업은 가능하지만 현재 code plan만 잘못된 상태입니다.

`INFEASIBLE`은 현재 helper나 skill 조건으로는 실행할 수 없다고 판단되는 상태입니다.

즉 validator는 로봇을 조종하는 코드가 아니라, LLM이 만든 action sequence가 실제 executor 제약을 만족하는지 미리 판정하는 코드입니다.

## 5번 슬라이드

여기서는 validator가 실제로 무엇을 확인하는지 예시로 설명하겠습니다.

왼쪽은 `Slice tomato`에서 문제가 됐던 형태의 code plan입니다. `robot1`이 Knife를 집은 다음, 다시 Tomato를 집으려고 합니다.

AI2-THOR에서는 agent가 손에 물체를 하나만 들 수 있기 때문에, 이 코드는 그대로 실행하면 실패합니다.

validator는 이 코드를 바로 실행하지 않고, helper 호출 순서만 따라갑니다.

첫 번째 줄에서 `PickupObject(robot1, Knife)`를 보면 내부 상태에 `robot1 hand = Knife`를 기록합니다.

그 다음 줄에서 같은 `robot1`이 Tomato를 다시 집으려고 하면, 이미 Knife를 들고 있으므로 `HAND_NOT_EMPTY` 오류가 만들어집니다.

이때 issue에는 단순 오류 문장만 들어가는 것이 아니라, `held_object = Knife`, `target_object = Tomato` 같은 details가 같이 저장됩니다.

그래서 `REPAIRABLE_PLAN_ERROR`는 작업 자체가 불가능하다는 뜻이 아니라, 생성된 `code_plan.py`가 executor 제약을 어긴 상태라는 의미입니다.

## 6번 슬라이드

다음은 방금 만든 issue가 repair feedback으로 바뀌는 방식입니다.

핵심은 `ValidationIssue`와 `ground truth`를 함께 본다는 점입니다.

`ValidationIssue`에는 오류 코드와 details가 들어갑니다. 그리고 `ground truth`에는 최종 목표가 상태 변화인지, 포함 관계인지가 들어 있습니다.

첫 번째 예시는 `Slice tomato`입니다. issue는 `HAND_NOT_EMPTY`이고, details에는 `held = Knife`, `target = Tomato`가 들어갑니다.

ground truth는 `Tomato = SLICED`입니다. 목표가 Tomato를 옮기는 것이 아니라 자르는 것이므로, Tomato를 집을 필요가 없습니다.

그래서 feedback은 `PickupObject(Tomato)`를 제거하라는 형태로 만들어집니다.

두 번째 예시는 `Throw Spatula`입니다. ground truth는 `GarbageCan contains Spatula`입니다.

현재 구현된 `ThrowObject`는 어디에 던질지 target 인자를 받지 않습니다. 그래서 목표 상태를 만족하는 방식으로 `PutObject(holder, Spatula, GarbageCan)`를 사용하라는 feedback을 만들었습니다.

정리하면, 오류 유형별 기본 개선 방향은 미리 정의하고, 실제 issue details와 ground truth를 끼워 넣어서 구체적인 feedback 문장으로 만드는 구조입니다.

## 7번 슬라이드

마지막으로 보완 후 결과를 정리하겠습니다.

`Slice tomato`에서는 `HAND_NOT_EMPTY`가 발생했습니다. 하지만 ground truth가 Tomato의 `SLICED` 상태였기 때문에 Tomato를 들 필요가 없다고 보고 `PickupObject(Tomato)`를 제거하는 방향으로 repair되었습니다.

`Wash lettuce`에서는 object ownership 문제가 있었습니다. 물체를 집은 로봇과 `PutObject`를 하는 로봇이 달라졌기 때문에, holder 기준으로 object action을 수행하도록 수정되었습니다.

`Throw Spatula`에서는 skill 또는 ownership 문제가 있었지만, ground truth가 `GarbageCan contains Spatula`였기 때문에 `ThrowObject`가 아니라 `PutObject`로도 목표를 만족할 수 있었습니다.

정리하면, 이번 보완은 SMART-LLM 구조를 바꾸는 것이 아니라 LLM 출력과 실제 실행 환경 사이에 검증과 피드백 계층을 추가한 것입니다.

그 결과 FloorPlan6의 세 작업에서 repair 후 실행 가능한 plan을 얻을 수 있었습니다.
