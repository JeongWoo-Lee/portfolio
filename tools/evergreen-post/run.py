#!/usr/bin/env python3
"""연금형 유튜브 채널 발굴 -> 지표 계산 -> 게시글 초안 생성 파이프라인.

  python run.py demo                          # 키 없이 오프라인 확인
  python run.py discover --topic 수면          # 주제어로 후보 발굴 + 랭킹
  python run.py post --handle DeepSleepGuide  # 채널 하나 전체 파이프라인
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import score
from ytdata import Channel, QuotaMeter, YouTubeClient


def _need(var: str) -> str:
    value = os.environ.get(var, "").strip()
    if not value:
        sys.exit(f"환경변수 {var}가 없습니다. .env.example을 참고해 설정하세요.")
    return value


def _slug(channel: Channel) -> str:
    return (channel.handle or channel.channel_id).lstrip("@")


def _outdir(root: Path, channel: Channel) -> Path:
    today = dt.date.today().isoformat()
    path = root / f"{today}_{_slug(channel)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_metrics(metrics: score.Metrics, outdir: Path) -> None:
    (outdir / "metrics.json").write_text(
        json.dumps(metrics.as_prompt_facts(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _emit_drafts(metrics: score.Metrics, outdir: Path, variants: int) -> None:
    import draft  # anthropic import를 필요할 때만

    print(f"\n  초안 생성 중 ({variants}편)...")
    posts = draft.write_variants(metrics, n=variants)
    for i, post in enumerate(posts, 1):
        name = "post.txt" if variants == 1 else f"post-{i}.txt"
        (outdir / name).write_text(post, encoding="utf-8")
        print(f"\n  ── 초안 {i} " + "─" * 50)
        print("\n".join("  " + line for line in post.splitlines()))
    print()


def _maybe_capture(metrics: score.Metrics, outdir: Path, headless: bool) -> None:
    handle = metrics.channel.handle
    if not handle:
        print("  [건너뜀] 핸들이 없는 채널이라 캡쳐를 생략합니다.")
        return
    import capture

    print("  스크린샷 촬영 중...")
    try:
        path = capture.capture_channel(handle, outdir / "channel.png", headless=headless)
        print(f"  저장됨: {path}")
    except Exception as exc:
        print(f"  [실패] 캡쳐 실패: {exc}")
        print("         (클라우드 세션이면 youtube.com이 차단돼 정상입니다. 로컬에서 실행하세요.)")


# ----------------------------------------------------------------------
# 명령어
# ----------------------------------------------------------------------
def cmd_demo(args: argparse.Namespace) -> None:
    import fixtures

    channel, videos = fixtures.deep_sleep_guide()
    metrics = score.compute(channel, videos)

    print("오프라인 데모 — 스크린샷에서 읽은 상위 6개 영상 기준\n")
    print(score.format_table([metrics]))
    print("\n점수 구성:")
    for key, value in metrics.breakdown.items():
        print(f"  {key:<12} {value:.3f}  (가중치 {score.WEIGHTS[key]})")
    if metrics.partial_data:
        print(f"\n  ※ 표본 {metrics.sample_size}개 / 전체 {channel.video_count}개 — "
              "부분 데이터라 reach·depth는 과소평가됩니다.")

    print("\n초안 생성기에 넘어갈 데이터:")
    print(json.dumps(metrics.as_prompt_facts(), ensure_ascii=False, indent=2))

    if args.draft:
        _need("ANTHROPIC_API_KEY")
        outdir = _outdir(Path(args.out), channel)
        _save_metrics(metrics, outdir)
        _emit_drafts(metrics, outdir, args.variants)


def cmd_discover(args: argparse.Namespace) -> None:
    meter = QuotaMeter(budget=args.quota)
    client = YouTubeClient(_need("YOUTUBE_API_KEY"), meter=meter)

    print(f"'{args.topic}' 주제로 {args.min_age}년 이상 된 인기 영상 검색 중... (100 units)")
    channel_ids = client.discover_channel_ids(
        args.topic, min_age_years=args.min_age, region=args.region, language=args.language
    )
    print(f"  후보 채널 {len(channel_ids)}개")

    channels = client.fetch_channels(channel_ids[: args.candidates])
    channels = [c for c in channels if (c.subscribers or 0) >= args.min_subs]
    print(f"  구독자 {args.min_subs:,}명 이상: {len(channels)}개\n")

    results: list[score.Metrics] = []
    for channel in channels:
        try:
            videos = client.fetch_channel_videos(channel, max_videos=args.max_videos)
            if not videos:
                continue
            results.append(score.compute(channel, videos))
            print(f"  분석 완료: {channel.title}")
        except Exception as exc:
            print(f"  [건너뜀] {channel.title}: {exc}")

    results.sort(key=lambda m: m.score, reverse=True)
    print(f"\n{score.format_table(results)}")
    print(f"\n사용 할당량: {meter.summary()}")

    top = results[: args.top]
    root = Path(args.out)
    for metrics in top:
        outdir = _outdir(root, metrics.channel)
        _save_metrics(metrics, outdir)
        if args.shot:
            _maybe_capture(metrics, outdir, headless=not args.headful)
        if args.draft:
            _emit_drafts(metrics, outdir, args.variants)
    if top:
        print(f"결과 저장 위치: {root.resolve()}")


def cmd_post(args: argparse.Namespace) -> None:
    meter = QuotaMeter(budget=args.quota)
    client = YouTubeClient(_need("YOUTUBE_API_KEY"), meter=meter)

    channel = client.resolve_handle(args.handle)
    if channel is None:
        sys.exit(f"채널을 찾을 수 없습니다: @{args.handle.lstrip('@')}")

    videos = client.fetch_channel_videos(channel, max_videos=args.max_videos)
    if not videos:
        sys.exit("영상을 가져오지 못했습니다.")

    metrics = score.compute(channel, videos)
    print(score.format_table([metrics]))
    print(f"\n사용 할당량: {meter.summary()}")

    outdir = _outdir(Path(args.out), channel)
    _save_metrics(metrics, outdir)
    if args.shot:
        _maybe_capture(metrics, outdir, headless=not args.headful)
    if args.draft:
        _emit_drafts(metrics, outdir, args.variants)
    print(f"결과 저장 위치: {outdir.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, *, draft_default: bool) -> None:
        p.add_argument("--out", default="out", help="결과 저장 디렉터리 (기본 out)")
        p.add_argument("--draft", action=argparse.BooleanOptionalAction,
                       default=draft_default, help="Claude로 초안 생성")
        p.add_argument("--variants", type=int, default=3, help="초안 개수 (기본 3)")
        p.add_argument("--quota", type=int, default=10_000, help="할당량 예산")
        p.add_argument("--max-videos", type=int, default=300,
                       help="채널당 수집할 영상 수 (기본 300)")

    def shot_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--shot", action=argparse.BooleanOptionalAction, default=True,
                       help="채널 스크린샷 촬영 (로컬 전용)")
        p.add_argument("--headful", action="store_true",
                       help="브라우저 창을 띄워서 실행 (봇 감지 회피에 유리)")

    d = sub.add_parser("demo", help="키 없이 오프라인 확인")
    common(d, draft_default=False)
    d.set_defaults(func=cmd_demo)

    s = sub.add_parser("discover", help="주제어로 연금형 채널 발굴")
    s.add_argument("--topic", required=True, help="검색 주제어 (예: 수면, 공부 집중)")
    s.add_argument("--min-age", type=float, default=3.0,
                   help="이 연수 이상 된 영상만 검색 (기본 3년)")
    s.add_argument("--min-subs", type=int, default=50_000, help="최소 구독자수")
    s.add_argument("--candidates", type=int, default=12, help="정밀 분석할 후보 수")
    s.add_argument("--top", type=int, default=3, help="결과를 저장할 상위 채널 수")
    s.add_argument("--region", default="KR")
    s.add_argument("--language", default="ko")
    common(s, draft_default=True)
    shot_opts(s)
    s.set_defaults(func=cmd_discover)

    p = sub.add_parser("post", help="채널 하나로 전체 파이프라인 실행")
    p.add_argument("--handle", required=True, help="채널 핸들 (예: DeepSleepGuide)")
    common(p, draft_default=True)
    shot_opts(p)
    p.set_defaults(func=cmd_post)

    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
