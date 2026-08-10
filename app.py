"""테트리스 — Streamlit 버전

원본 tetris.html의 게임 규칙(SRS 회전과 킥, 7-bag, 홀드, 고스트, 락 딜레이,
점수·레벨 체계)을 파이썬으로 이식했다. 렌더링은 CSS 그리드, 중력은 st.fragment의
자동 재실행으로 처리한다.

실행:  streamlit run app.py
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

# ---------------------------------------------------------------- 상수

COLS, ROWS = 10, 20

SHAPES: dict[str, list[list[int]]] = {
    "I": [[0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0], [0, 0, 0, 0]],
    "O": [[1, 1], [1, 1]],
    "T": [[0, 1, 0], [1, 1, 1], [0, 0, 0]],
    "S": [[0, 1, 1], [1, 1, 0], [0, 0, 0]],
    "Z": [[1, 1, 0], [0, 1, 1], [0, 0, 0]],
    "J": [[1, 0, 0], [1, 1, 1], [0, 0, 0]],
    "L": [[0, 0, 1], [1, 1, 1], [0, 0, 0]],
}

COLORS: dict[str, str] = {
    "I": "#4ad6e8", "O": "#f2c94c", "T": "#b57bff", "S": "#5ad67d",
    "Z": "#f2685f", "J": "#5a8dee", "L": "#f2a04c",
}

# SRS 킥 테이블. 키는 (회전 전, 회전 후) 상태.
# 원본 규격은 y축 위쪽이 +라서, 화면 좌표(아래쪽이 +)에 적용할 때 y 부호를 뒤집는다.
KICKS_JLSTZ: dict[tuple[int, int], list[tuple[int, int]]] = {
    (0, 1): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (1, 0): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (1, 2): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (2, 1): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (2, 3): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
    (3, 2): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (3, 0): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (0, 3): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
}
KICKS_I: dict[tuple[int, int], list[tuple[int, int]]] = {
    (0, 1): [(0, 0), (-2, 0), (1, 0), (-2, -1), (1, 2)],
    (1, 0): [(0, 0), (2, 0), (-1, 0), (2, 1), (-1, -2)],
    (1, 2): [(0, 0), (-1, 0), (2, 0), (-1, 2), (2, -1)],
    (2, 1): [(0, 0), (1, 0), (-2, 0), (1, -2), (-2, 1)],
    (2, 3): [(0, 0), (2, 0), (-1, 0), (2, 1), (-1, -2)],
    (3, 2): [(0, 0), (-2, 0), (1, 0), (-2, -1), (1, 2)],
    (3, 0): [(0, 0), (1, 0), (-2, 0), (1, -2), (-2, 1)],
    (0, 3): [(0, 0), (-1, 0), (2, 0), (-1, 2), (2, -1)],
}

GRAVITY_MS = [800, 720, 630, 550, 470, 380, 300, 220, 130, 100,
              83, 83, 83, 67, 67, 67, 50, 50, 50, 33, 33, 33, 33, 17]

LINE_SCORE = [0, 100, 300, 500, 800]

LOCK_DELAY = 0.5          # 접지 후 고정까지의 유예(초)
MAX_LOCK_RESETS = 15      # 유예를 되돌릴 수 있는 최대 횟수
TICK = 0.12               # 화면 갱신 주기(초)

# Streamlit은 재실행마다 왕복이 필요해 이보다 짧은 간격은 의미가 없다.
# 높은 레벨에서 원본보다 느려지지만, 대신 블록이 여러 칸씩 순간이동하지 않는다.
MIN_DROP_INTERVAL = 0.10
MAX_STEPS_PER_UPDATE = 3


# ---------------------------------------------------------------- 게임

def rotate_cw(m: list[list[int]]) -> list[list[int]]:
    n = len(m)
    return [[m[n - 1 - x][y] for x in range(n)] for y in range(n)]


@dataclass
class Piece:
    kind: str
    matrix: list[list[int]]
    rot: int
    x: int
    y: int


@dataclass
class Tetris:
    seed: Optional[int] = None
    base_level: int = 1

    grid: list[list[Optional[str]]] = field(default_factory=list)
    queue: list[str] = field(default_factory=list)
    bag: list[str] = field(default_factory=list)
    hold: Optional[str] = None
    can_hold: bool = True
    current: Optional[Piece] = None

    score: int = 0
    lines: int = 0
    level: int = 1
    game_over: bool = False
    paused: bool = False
    last_clear: int = 0

    grounded_at: Optional[float] = None
    lock_resets: int = 0
    next_drop: Optional[float] = None

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.grid = [[None] * COLS for _ in range(ROWS)]
        self.level = max(1, self.base_level)
        self._fill_queue()
        self.spawn()

    # ---- 블록 공급 (7-bag) ----

    def _next_kind(self) -> str:
        if not self.bag:
            self.bag = list(SHAPES.keys())
            self.rng.shuffle(self.bag)
        return self.bag.pop(0)

    def _fill_queue(self) -> None:
        while len(self.queue) < 3:
            self.queue.append(self._next_kind())

    def spawn(self, kind: Optional[str] = None) -> None:
        if kind is None:
            kind = self.queue.pop(0)
            self._fill_queue()
        matrix = [row[:] for row in SHAPES[kind]]
        self.current = Piece(
            kind=kind,
            matrix=matrix,
            rot=0,
            x=(COLS - len(matrix)) // 2,
            y=-1 if kind == "I" else 0,
        )
        self.grounded_at = None
        self.lock_resets = 0
        if self.collides(self.current):
            self.game_over = True

    # ---- 판정 ----

    def collides(self, piece: Piece, dx: int = 0, dy: int = 0,
                 matrix: Optional[list[list[int]]] = None) -> bool:
        m = piece.matrix if matrix is None else matrix
        for y, row in enumerate(m):
            for x, filled in enumerate(row):
                if not filled:
                    continue
                nx, ny = piece.x + x + dx, piece.y + y + dy
                if nx < 0 or nx >= COLS or ny >= ROWS:
                    return True
                if ny >= 0 and self.grid[ny][nx] is not None:
                    return True
        return False

    def _touch_lock(self) -> None:
        """접지 중 조작하면 락 딜레이를 되돌린다(횟수 제한)."""
        if self.grounded_at is not None and self.lock_resets < MAX_LOCK_RESETS:
            self.grounded_at = None
            self.lock_resets += 1

    # ---- 조작 ----

    def move(self, dx: int) -> bool:
        if self.game_over or self.current is None:
            return False
        if self.collides(self.current, dx, 0):
            return False
        self.current.x += dx
        self._touch_lock()
        return True

    def rotate(self, direction: int) -> bool:
        if self.game_over or self.current is None:
            return False

        turns = 1 if direction > 0 else 3
        m = self.current.matrix
        for _ in range(turns):
            m = rotate_cw(m)

        to_rot = (self.current.rot + turns) % 4
        if self.current.kind == "O":
            kicks = [(0, 0)]
        else:
            table = KICKS_I if self.current.kind == "I" else KICKS_JLSTZ
            kicks = table[(self.current.rot, to_rot)]

        for kx, ky in kicks:
            if self.collides(self.current, kx, -ky, m):
                continue
            self.current.matrix = m
            self.current.rot = to_rot
            self.current.x += kx
            self.current.y += -ky
            self._touch_lock()
            return True
        return False

    def soft_drop(self) -> None:
        if self.game_over or self.current is None:
            return
        if self.collides(self.current, 0, 1):
            self.lock()
            return
        self.current.y += 1
        self.score += 1
        self.next_drop = None

    def hard_drop(self) -> None:
        if self.game_over or self.current is None:
            return
        dist = 0
        while not self.collides(self.current, 0, 1):
            self.current.y += 1
            dist += 1
        self.score += dist * 2
        self.lock()

    def hold_piece(self) -> None:
        if self.game_over or not self.can_hold:
            return
        assert self.current is not None
        previous = self.hold
        self.hold = self.current.kind
        self.can_hold = False
        self.spawn(previous)

    # ---- 고정과 줄 삭제 ----

    def lock(self) -> None:
        assert self.current is not None
        piece = self.current
        for y, row in enumerate(piece.matrix):
            for x, filled in enumerate(row):
                if not filled:
                    continue
                gy, gx = piece.y + y, piece.x + x
                if gy < 0:
                    self.game_over = True
                    return
                self.grid[gy][gx] = piece.kind

        cleared = self.clear_lines()
        self.last_clear = cleared
        if cleared:
            self.score += LINE_SCORE[cleared] * self.level
            self.lines += cleared
            self.level = self.base_level + self.lines // 10

        self.can_hold = True
        self.next_drop = None
        self.spawn()

    def clear_lines(self) -> int:
        kept = [row for row in self.grid if not all(cell is not None for cell in row)]
        cleared = ROWS - len(kept)
        if cleared:
            self.grid = [[None] * COLS for _ in range(cleared)] + kept
        return cleared

    # ---- 진행 ----

    def drop_interval(self) -> float:
        idx = min(self.level - 1, len(GRAVITY_MS) - 1)
        return max(GRAVITY_MS[idx] / 1000, MIN_DROP_INTERVAL)

    def ghost_offset(self) -> int:
        if self.current is None:
            return 0
        d = 0
        while not self.collides(self.current, 0, d + 1):
            d += 1
        return d

    def update(self, now: float) -> None:
        """중력과 락 딜레이를 진행시킨다. 화면 갱신마다 호출한다."""
        if self.game_over or self.paused or self.current is None:
            return

        if self.next_drop is None:
            self.next_drop = now + self.drop_interval()

        if self.collides(self.current, 0, 1):
            # 접지 상태 — 유예 시간 동안 밀어 넣을 여지를 준다
            if self.grounded_at is None:
                self.grounded_at = now
            elif now - self.grounded_at >= LOCK_DELAY:
                self.lock()
                self.next_drop = now + self.drop_interval()
            return

        self.grounded_at = None

        steps = 0
        while now >= self.next_drop and steps < MAX_STEPS_PER_UPDATE:
            if self.collides(self.current, 0, 1):
                break
            self.current.y += 1
            self.next_drop += self.drop_interval()
            steps += 1

        # 탭이 비활성화됐다 돌아온 경우처럼 크게 밀렸으면 타이머를 다시 잡는다
        if now - self.next_drop > 1.0:
            self.next_drop = now + self.drop_interval()


# ---------------------------------------------------------------- 렌더링

STYLE = """
<style>
  .tetris-board {
    display: grid;
    grid-template-columns: repeat(10, 26px);
    grid-auto-rows: 26px;
    gap: 1px;
    padding: 5px;
    background: #171a29;
    border: 1px solid #262a3f;
    border-radius: 9px;
    width: max-content;
  }
  .tc { border-radius: 3px; }
  .tc.empty { background: #0b0d17; }
  .tc.ghost { opacity: .26; }
  .tetris-prev {
    display: grid;
    grid-template-columns: repeat(4, 17px);
    grid-auto-rows: 17px;
    gap: 1px;
    padding: 5px;
    background: #171a29;
    border: 1px solid #262a3f;
    border-radius: 8px;
    width: max-content;
    margin-bottom: 7px;
  }
  .pc { border-radius: 2px; background: transparent; }
  .tetris-label {
    font-size: 10px; letter-spacing: .14em; text-transform: uppercase;
    color: #8b90ad; font-weight: 600; margin: 6px 0 4px;
  }
  .tetris-banner {
    padding: 9px 14px; border-radius: 8px; font-weight: 700;
    text-align: center; margin-bottom: 8px;
  }
  .tetris-banner.over { background: rgba(242,104,95,.18); color: #f2685f; }
  .tetris-banner.pause { background: rgba(110,231,255,.15); color: #6ee7ff; }
</style>
"""


def board_html(game: Tetris) -> str:
    """보드 + 고스트 + 현재 블록을 하나의 CSS 그리드로 그린다."""
    cells: list[list[Optional[tuple[str, bool]]]] = [
        [None] * COLS for _ in range(ROWS)
    ]

    for y in range(ROWS):
        for x in range(COLS):
            kind = game.grid[y][x]
            if kind is not None:
                cells[y][x] = (COLORS[kind], False)

    piece = game.current
    if piece is not None and not game.game_over:
        ghost = game.ghost_offset()
        color = COLORS[piece.kind]

        for y, row in enumerate(piece.matrix):
            for x, filled in enumerate(row):
                if not filled:
                    continue
                gy, gx = piece.y + y + ghost, piece.x + x
                if 0 <= gy < ROWS and 0 <= gx < COLS and cells[gy][gx] is None:
                    cells[gy][gx] = (color, True)

        for y, row in enumerate(piece.matrix):
            for x, filled in enumerate(row):
                if not filled:
                    continue
                gy, gx = piece.y + y, piece.x + x
                if 0 <= gy < ROWS and 0 <= gx < COLS:
                    cells[gy][gx] = (color, False)

    parts = ['<div class="tetris-board">']
    for row in cells:
        for cell in row:
            if cell is None:
                parts.append('<div class="tc empty"></div>')
            else:
                color, is_ghost = cell
                cls = "tc ghost" if is_ghost else "tc"
                parts.append(f'<div class="{cls}" style="background:{color}"></div>')
    parts.append("</div>")
    return "".join(parts)


def preview_html(kind: Optional[str]) -> str:
    """4×4 칸에 미노 하나를 중앙 정렬해 그린다.

    행렬을 그대로 그리면 I나 O가 한쪽으로 치우쳐 보이므로,
    실제로 채워진 칸만 골라내 가운데에 배치한다.
    """
    grid = [[False] * 4 for _ in range(4)]
    color = "#000000"

    if kind is not None:
        matrix = SHAPES[kind]
        color = COLORS[kind]
        filled = [(x, y) for y, row in enumerate(matrix)
                  for x, v in enumerate(row) if v]
        min_x = min(p[0] for p in filled)
        max_x = max(p[0] for p in filled)
        min_y = min(p[1] for p in filled)
        max_y = max(p[1] for p in filled)
        off_x = (4 - (max_x - min_x + 1)) // 2
        off_y = (4 - (max_y - min_y + 1)) // 2
        for x, y in filled:
            grid[y - min_y + off_y][x - min_x + off_x] = True

    parts = ['<div class="tetris-prev">']
    for row in grid:
        for on in row:
            style = f"background:{color}" if on else ""
            parts.append(f'<div class="pc" style="{style}"></div>')
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------- 조작

def do(action: str) -> None:
    """버튼 콜백. 화면을 다시 그리기 전에 상태를 바꾸기 위해 on_click으로 연결한다."""
    if action == "restart":
        st.session_state.game = Tetris(base_level=st.session_state.get("start_level", 1))
        return

    game: Tetris = st.session_state.game

    if action == "pause":
        if not game.game_over:
            game.paused = not game.paused
            game.next_drop = None
            game.grounded_at = None
        return

    if game.game_over or game.paused:
        return

    if action == "left":
        game.move(-1)
    elif action == "right":
        game.move(1)
    elif action == "down":
        game.soft_drop()
    elif action == "drop":
        game.hard_drop()
    elif action == "cw":
        game.rotate(1)
    elif action == "ccw":
        game.rotate(-1)
    elif action == "hold":
        game.hold_piece()


# 키보드 → 버튼 클릭 다리. Streamlit에는 키 이벤트 API가 없어서,
# 컴포넌트 iframe에서 부모 문서의 버튼을 찾아 클릭한다.
# 배포 환경에 따라 동작하지 않을 수 있으므로 버튼 조작이 기본 수단이다.
KEYBOARD_BRIDGE = """
<script>
(function () {
  try {
    var doc = window.parent.document;
    if (doc.__tetrisKeyBridge) return;
    doc.__tetrisKeyBridge = true;

    var MAP = {
      ArrowLeft: '◀', ArrowRight: '▶', ArrowDown: '▼',
      ArrowUp: '↻', x: '↻', X: '↻', z: '↺', Z: '↺',
      ' ': '⤓', c: 'HOLD', C: 'HOLD', p: '일시정지', P: '일시정지',
      r: '다시 시작', R: '다시 시작'
    };

    doc.addEventListener('keydown', function (e) {
      var label = MAP[e.key];
      if (!label) return;
      e.preventDefault();
      var buttons = doc.querySelectorAll('button');
      for (var i = 0; i < buttons.length; i++) {
        if (buttons[i].innerText.trim() === label) { buttons[i].click(); return; }
      }
    });
  } catch (err) {
    /* 다른 오리진이면 접근할 수 없다. 버튼으로 조작하면 된다. */
  }
})();
</script>
"""


# ---------------------------------------------------------------- 화면

def _auto_refresh(run_every: float):
    """주기적으로 다시 그려주는 데코레이터를 고른다.

    st.fragment는 1.37부터 정식 API이고 그 전에는 experimental_fragment였다.
    둘 다 없는 아주 낮은 버전에서는 그대로 통과시켜, 자동 낙하는 안 되더라도
    버튼 조작만으로는 플레이할 수 있게 한다.
    """
    deco = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    if deco is None:
        return lambda fn: fn
    return deco(run_every=run_every)


HAS_AUTO_REFRESH = (getattr(st, "fragment", None)
                    or getattr(st, "experimental_fragment", None)) is not None


@_auto_refresh(TICK)
def render_game() -> None:
    game: Tetris = st.session_state.game
    game.update(time.monotonic())

    if game.game_over and game.score > st.session_state.best:
        st.session_state.best = game.score

    if game.game_over:
        st.markdown('<div class="tetris-banner over">게임 오버 — 다시 시작을 누르세요</div>',
                    unsafe_allow_html=True)
    elif game.paused:
        st.markdown('<div class="tetris-banner pause">일시정지</div>',
                    unsafe_allow_html=True)

    board_col, side_col = st.columns([3, 2], gap="medium")

    with board_col:
        st.markdown(board_html(game), unsafe_allow_html=True)

    with side_col:
        st.markdown('<div class="tetris-label">Hold</div>', unsafe_allow_html=True)
        st.markdown(preview_html(game.hold), unsafe_allow_html=True)

        st.markdown('<div class="tetris-label">Next</div>', unsafe_allow_html=True)
        for kind in game.queue[:3]:
            st.markdown(preview_html(kind), unsafe_allow_html=True)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("점수", f"{game.score:,}")
    m2.metric("레벨", game.level)
    m3.metric("라인", game.lines)
    m4.metric("최고 점수", f"{st.session_state.best:,}")

    st.markdown('<div class="tetris-label">조작</div>', unsafe_allow_html=True)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.button("◀", on_click=do, args=("left",), use_container_width=True,
              help="왼쪽으로 이동 (←)")
    c2.button("▼", on_click=do, args=("down",), use_container_width=True,
              help="소프트 드롭 (↓)")
    c3.button("▶", on_click=do, args=("right",), use_container_width=True,
              help="오른쪽으로 이동 (→)")
    c4.button("↺", on_click=do, args=("ccw",), use_container_width=True,
              help="반시계 방향 회전 (Z)")
    c5.button("↻", on_click=do, args=("cw",), use_container_width=True,
              help="시계 방향 회전 (↑ 또는 X)")

    d1, d2, d3, d4 = st.columns(4)
    d1.button("⤓", on_click=do, args=("drop",), use_container_width=True,
              type="primary", help="하드 드롭 (Space)")
    d2.button("HOLD", on_click=do, args=("hold",), use_container_width=True,
              help="홀드 (C)")
    d3.button("일시정지", on_click=do, args=("pause",), use_container_width=True,
              help="일시정지 (P)")
    d4.button("다시 시작", on_click=do, args=("restart",), use_container_width=True,
              help="다시 시작 (R)")


def main() -> None:
    st.set_page_config(page_title="테트리스", page_icon="🎮", layout="centered")
    st.markdown(STYLE, unsafe_allow_html=True)

    if "best" not in st.session_state:
        st.session_state.best = 0
    if "start_level" not in st.session_state:
        st.session_state.start_level = 1
    if "game" not in st.session_state:
        st.session_state.game = Tetris(base_level=st.session_state.start_level)

    st.title("🎮 테트리스")

    with st.sidebar:
        st.header("설정")
        st.slider("시작 레벨", 1, 15, key="start_level",
                  help="레벨이 높을수록 처음부터 빠르게 떨어집니다. 다시 시작해야 적용됩니다.")
        st.button("이 설정으로 새 게임", on_click=do, args=("restart",),
                  use_container_width=True, type="primary")

        st.divider()
        st.subheader("조작")
        st.markdown(
            "- **← →** 이동\n"
            "- **↓** 소프트 드롭 (1점/칸)\n"
            "- **Space** 하드 드롭 (2점/칸)\n"
            "- **↑ / X** 시계 방향 회전\n"
            "- **Z** 반시계 방향 회전\n"
            "- **C** 홀드 (블록당 1회)\n"
            "- **P** 일시정지 · **R** 다시 시작"
        )
        st.caption(
            "키보드가 듣지 않으면 화면의 버튼으로 조작하세요. "
            "Streamlit에는 키 입력 API가 없어 키보드는 보조 수단으로 붙여 두었습니다."
        )

        st.divider()
        st.subheader("점수")
        st.markdown(
            "| 지운 줄 | 점수 |\n|---|---|\n"
            "| 1줄 | 100 × 레벨 |\n| 2줄 | 300 × 레벨 |\n"
            "| 3줄 | 500 × 레벨 |\n| 4줄 | 800 × 레벨 |"
        )
        st.caption("10줄마다 레벨이 오르고 낙하가 빨라집니다.")

    components.html(KEYBOARD_BRIDGE, height=0)

    if not HAS_AUTO_REFRESH:
        st.warning(
            "Streamlit 버전이 낮아 자동 낙하가 동작하지 않습니다. "
            "`pip install -U streamlit` 으로 1.37 이상으로 올려주세요. "
            "그때까지는 ▼ 버튼으로 직접 내려야 합니다."
        )

    # 중력에는 주기적인 재실행이 필요하다. fragment는 이 영역만 다시 그리므로
    # 페이지 전체를 재실행하는 것보다 훨씬 가볍다.
    render_game()

    st.caption(
        "Streamlit은 상호작용마다 스크립트를 다시 실행하는 구조라 60fps 게임에는 맞지 않습니다. "
        f"이 버전은 {int(TICK * 1000)}ms 주기로 화면을 갱신하며, "
        f"낙하 간격도 최소 {int(MIN_DROP_INTERVAL * 1000)}ms로 제한합니다."
    )


if __name__ == "__main__":
    main()
