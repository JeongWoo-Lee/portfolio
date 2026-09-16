"""수집한 지표를 근거로 X(트위터) 게시글 초안을 쓴다.

설계 원칙 하나: 모델은 숫자를 만들어내면 안 된다.
프롬프트에 들어가는 모든 수치는 score.Metrics.as_prompt_facts()에서만 오고,
시스템 프롬프트가 "여기 없는 숫자는 쓰지 말라"고 명시적으로 막는다.
"""

from __future__ import annotations

import json

import anthropic

from score import Metrics

MODEL = "claude-opus-5"

# 사용자가 참고용으로 제시한 @mad_dogdebt 게시글. 구조/문체 앵커로만 쓴다.
STYLE_ANCHOR = """유튜브에서 연금 같은 채널이 바로 이런 채널들

'브레이너 제이의 숙면 여행'

수면 유도 음악들을 들려주는 컨셉인데, 4~6년 전에 올린 영상들이 조회수 1천만~2천만회를 넘김

이런 채널들의 강점은 굳이 최신 영상을 볼 이유가 없다는 것

5년 전 음악이라고 잠이 덜 오는 것도 아니고, 자기한테 잘 맞으면 매일 밤 같은 영상을 틀어놓을 수도 있으니까

한 번 보고 끝나는 콘텐츠가 아니라 계속 다시 찾게 되는 콘텐츠인 셈. 물론 자리 잡기까지가 어렵겠지만, 이렇게 쌓아둔 영상들이 오래 소비되는 구조가 진짜 연금 같은 구조.

AI음악으로 이런 분야를 개척하는 사람도 있지 않을까 싶음."""

SYSTEM = f"""당신은 한국어 X(트위터) 게시글을 쓰는 작성자다.
유튜브 채널 데이터를 받아서, 그 채널이 왜 '연금형'인지 분석하는 짧은 글을 쓴다.

## 글의 구조 (이 순서를 지킬 것)
1. 훅 — 단언하는 한 줄. 제목처럼 쓴다.
2. 사례 지목 — 채널명을 작은따옴표로 감싼다.
3. 숫자 증거 — 채널 컨셉 한 줄 + 제공된 조회수/경과연수를 자연스럽게 녹인다.
4. 통찰 — 이 채널이 왜 계속 소비되는지, 콘텐츠 성격에서 이유를 찾는다.
5. 부연 — 4번을 독자 입장의 구체적 장면으로 풀어준다.
6. 일반화 — 개별 채널을 넘어 원리로 확장한다. 반론("물론 ~은 어렵겠지만")을 한 번 인정한다.
7. 열린 마무리 — 앞으로의 가능성을 던지는 한 문장.

## 문체
- 음슴체 위주: "~함", "~음", "~인 셈", "~싶음". 존댓말 금지.
- 해시태그, 이모지, 불릿, 굵은 글씨 전부 금지. 평문만.
- 문단은 빈 줄로 구분하고, 한 문단은 1~3문장.
- 전체 400~700자.
- 단정적이되 과장하지 않는다. "대박", "미쳤다" 같은 표현은 안 쓴다.

## 숫자 규칙 (가장 중요)
- 사용자가 준 데이터에 있는 숫자만 쓴다. 없는 숫자는 절대 만들지 않는다.
- 조회수는 한국식으로 반올림해 읽는다. 26,210,000 -> "2600만" 또는 "2천만회".
  개별 수치를 나열하기보다 "4~6년 전 영상들이 1천만~2천만회" 처럼 구간으로 묶는 게 낫다.
- 데이터에 `데이터_불완전: true`가 있으면 표본이 채널 전체가 아니라는 뜻이다.
  이때는 "상위 영상 기준" 같은 한정어를 붙이거나, 아예 개별 수치만 인용한다.
- 구독자수가 null이면 구독자 얘기를 꺼내지 않는다.
- 연금점수/점수_구성은 내부 계산용 참고값이다. 글에 숫자로 인용하지 말 것.

## 참고 게시글 (문체와 구조만 참고)
아래는 같은 장르의 글이다. 리듬과 전개를 참고하되, 표현을 그대로 가져다 쓰지 말 것.
특히 훅 문장과 마지막 문장은 반드시 새로 쓴다.

---
{STYLE_ANCHOR}
---

출력은 게시글 본문만. 설명, 머리말, 따옴표 감싸기 없이 글만 출력한다."""


def _extract_text(response) -> str:
    parts = [b.text for b in response.content if b.type == "text"]
    return "\n".join(parts).strip()


def write_post(
    metrics: Metrics,
    client: anthropic.Anthropic | None = None,
    angle: str | None = None,
    effort: str = "high",
) -> str:
    """지표 하나를 받아 게시글 초안 한 편을 만든다."""
    client = client or anthropic.Anthropic()
    facts = metrics.as_prompt_facts()

    user_parts = [
        "아래 데이터로 게시글을 써라.",
        "",
        "```json",
        json.dumps(facts, ensure_ascii=False, indent=2),
        "```",
    ]
    if angle:
        user_parts += ["", f"추가 요청: {angle}"]

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        # 정책 거절 시 같은 요청을 폴백 모델로 다시 태운다. 이 용도에서 거절이
        # 날 일은 사실상 없지만, 파이프라인이 조용히 빈 결과를 내는 것보단 낫다.
        betas=["server-side-fallback-2026-06-01"],
        fallbacks=[{"model": "claude-opus-4-8"}],
        messages=[{"role": "user", "content": "\n".join(user_parts)}],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(
            "모델이 요청을 거절했습니다 (폴백까지 전부). "
            f"stop_details={getattr(response, 'stop_details', None)}"
        )

    text = _extract_text(response)
    if not text:
        raise RuntimeError(f"빈 응답 (stop_reason={response.stop_reason})")
    return text


def write_variants(metrics: Metrics, n: int = 3, **kwargs) -> list[str]:
    """같은 데이터로 각도를 달리한 초안 여러 개. 골라 쓰라고 만든다."""
    angles = [
        None,
        "훅을 질문형으로 바꿔서 써라.",
        "6번 일반화 단계에 더 무게를 싣고, 개별 채널 소개는 짧게 처리해라.",
        "이 구조를 따라하려는 사람 입장에서 뭘 해야 하는지에 초점을 맞춰라.",
    ]
    client = anthropic.Anthropic()
    return [
        write_post(metrics, client=client, angle=angles[i % len(angles)], **kwargs)
        for i in range(n)
    ]
