"""채널 '동영상' 탭을 인기순으로 정렬해 스크린샷을 찍는다.

이 모듈만은 로컬 PC에서 돌려야 한다. Claude Code 클라우드 세션은
youtube.com 자체가 네트워크 정책으로 막혀 있어서 페이지가 안 열린다.
(API 호출은 youtube.googleapis.com이라 별개로 잘 된다.)

주의: 유튜브 DOM은 예고 없이 바뀐다. 아래 셀렉터들은 2026-09 기준이고,
정렬 칩을 못 찾으면 sort=p URL 파라미터로 폴백한 뒤 경고를 띄운다.
"""

from __future__ import annotations

import time
from pathlib import Path

# 정렬 칩 텍스트 (한/영 UI 모두 대응)
POPULAR_LABELS = ["인기순", "Popular", "Most popular"]
CONSENT_LABELS = ["모두 수락", "Accept all", "Reject all", "모두 거부"]

GRID_SELECTORS = [
    "ytd-rich-grid-renderer",
    "#contents.ytd-rich-grid-renderer",
    "ytd-two-column-browse-results-renderer",
]


def _dismiss_consent(page) -> bool:
    """EU/신규 프로필에서 뜨는 쿠키 동의 화면을 닫는다."""
    for label in CONSENT_LABELS:
        try:
            button = page.get_by_role("button", name=label)
            if button.count() > 0:
                button.first.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                return True
        except Exception:
            continue
    return False


def _sort_by_popular(page) -> bool:
    """정렬 칩에서 '인기순'을 누른다. 성공하면 True."""
    for label in POPULAR_LABELS:
        try:
            chip = page.get_by_role("tab", name=label)
            if chip.count() == 0:
                chip = page.get_by_text(label, exact=True)
            if chip.count() > 0:
                chip.first.click(timeout=5000)
                page.wait_for_timeout(2500)  # 그리드 재렌더 대기
                return True
        except Exception:
            continue
    return False


def capture_channel(
    handle: str,
    out_path: str | Path,
    headless: bool = True,
    rows: int = 2,
    locale: str = "ko-KR",
    timeout_ms: int = 45_000,
) -> Path:
    """채널 동영상 탭(인기순)을 캡쳐해서 out_path에 저장하고 경로를 돌려준다.

    rows: 세로로 몇 줄까지 담을지. 참고 게시글은 2줄(6개)짜리였다.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "playwright가 없습니다.  pip install playwright && playwright install chromium"
        ) from exc

    handle = handle.lstrip("@")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    url = f"https://www.youtube.com/@{handle}/videos"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            locale=locale,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )
        # 동의 화면을 미리 넘기는 쿠키. 실패해도 _dismiss_consent가 받아준다.
        context.add_cookies([{
            "name": "SOCS", "value": "CAI",
            "domain": ".youtube.com", "path": "/",
        }])

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        _dismiss_consent(page)

        try:
            page.wait_for_selector("ytd-rich-grid-renderer", timeout=timeout_ms)
        except Exception:
            pass  # 아래에서 full page로 폴백

        if not _sort_by_popular(page):
            # 폴백: 오래된 URL 파라미터. 최신 UI에서 무시될 수 있어 경고를 남긴다.
            print("  [경고] '인기순' 칩을 못 찾아 sort=p 파라미터로 폴백합니다. "
                  "정렬이 최신순 그대로일 수 있으니 결과 이미지를 꼭 확인하세요.")
            page.goto(f"{url}?view=0&sort=p&flow=grid",
                      wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)

        # 썸네일 lazy-load를 깨우고 맨 위로 복귀
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(1500)
        page.mouse.wheel(0, -2000)
        page.wait_for_timeout(1200)

        target = None
        for selector in GRID_SELECTORS:
            loc = page.locator(selector)
            if loc.count() > 0:
                target = loc.first
                break

        if target is not None:
            box = target.bounding_box()
            if box:
                # 카드 한 줄이 대략 330px. rows 줄 + 채널 헤더 여유분.
                height = min(box["height"], rows * 330 + 60)
                page.screenshot(
                    path=str(out_path),
                    clip={"x": box["x"], "y": box["y"],
                          "width": box["width"], "height": height},
                )
            else:
                target.screenshot(path=str(out_path))
        else:
            page.screenshot(path=str(out_path))

        context.close()
        browser.close()

    return out_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("사용법: python capture.py <핸들> [출력경로] [--headful]")
        raise SystemExit(1)
    dest = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") \
        else f"out/{sys.argv[1].lstrip('@')}.png"
    started = time.time()
    path = capture_channel(sys.argv[1], dest, headless="--headful" not in sys.argv)
    print(f"저장됨: {path}  ({time.time() - started:.1f}s)")
