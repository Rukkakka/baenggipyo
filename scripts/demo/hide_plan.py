"""Claude 데모 GIF 에서 Claude Code 배너의 요금제 글자(`· Claude Pro`)를 배경색으로 덮는다.

Claude Code 배너는 모델 옆에 요금제를 늘 보여 주고 끄는 설정이 없다. `claude.tape` 로 녹화한 뒤
`python3 scripts/demo/hide_plan.py docs/demo/claude.gif` 로 덮는다. ffmpeg 가 필요하다.

로고(분홍 몸통)가 제자리에 보이는 장면만 덮는다. 로고가 다른 줄에 보이면(배너가 밀렸거나 일부만 남았으면) 요금제 글자도
덮는 칸 밖에 있으므로 덮지 않고 멈춘다.
좌표는 setup.tape 의 화면 설정(1200x880·D2Coding 17·여백 24)에서 'Opus 5.5 with medium effort · Claude Pro' 를 잰 값이다.
모델·생각 깊이 글자가 바뀌면 PLAN_BOX 의 x 를, 화면 설정이 바뀌면 모든 좌표를 다시 잰다.
"""

import subprocess
import sys
from pathlib import Path


# 덮을 칸 (x, y, 너비, 높이): '· Claude Pro' 는 x 387~492, y 62~74
PLAN_BOX: tuple[int, int, int, int] = (380, 58, 125, 22)
# 로고를 찾을 세로 띠 (x, 너비). 배너가 밀려도 찾도록 화면 높이 전체를 본다
LOGO_STRIP: tuple[int, int] = (24, 88)
HEIGHT: int = 880
# 제자리에 있는 로고 몸통의 첫 줄과 끝 줄. 몸통 줄은 띠 안의 분홍 픽셀이 47개 이상이고, 다리 줄과 글자(스피너·커서) 줄은
# 33개 이하다. 첫 줄·끝 줄은 색이 번져 녹화·인코딩마다 분홍 판정이 갈리므로 1px 차이는 제자리로 본다
# (배너가 밀리면 글자 한 줄인 20px 쯤 움직인다)
LOGO_ROWS: tuple[int, int] = (43, 78)
LOGO_ROW_PIXELS: int = 40
BACKGROUND: str = '0x1d1d2d'

# 분홍(R > 180, G < 150, B < 150) 판정을 픽셀마다 Python 으로 비교하지 않고 translate 로 한 번에 하는 바이트 표
RED: bytes = bytes(int(value > 180) for value in range(256))
LOW: bytes = bytes(int(value < 150) for value in range(256))


def logo_rows(frame: bytes, width: int) -> list[int]:
    """장면에서 로고 몸통처럼 분홍 픽셀이 빽빽한 줄 번호."""
    pixels = len(frame) // 3
    # 채널별 0/1 바이트열을 큰 정수로 바꿔 AND 하면 분홍 픽셀만 1 인 바이트열이 된다
    pink = (
        int.from_bytes(frame[0::3].translate(RED), 'big')
        & int.from_bytes(frame[1::3].translate(LOW), 'big')
        & int.from_bytes(frame[2::3].translate(LOW), 'big')
    ).to_bytes(pixels, 'big')
    return [y for y in range(pixels // width) if pink[y * width:(y + 1) * width].count(1) >= LOGO_ROW_PIXELS]


def logo_frames(gif: Path) -> list[list[int]]:
    """장면마다 로고 몸통 줄. GIF 를 한 번만 풀어 장면을 차례로 읽는다."""
    x, width = LOGO_STRIP
    size = width * HEIGHT * 3
    rows: list[list[int]] = []
    with subprocess.Popen(
        ['ffmpeg', '-v', 'error', '-i', str(gif), '-fps_mode', 'passthrough', '-vf', f'crop={width}:{HEIGHT}:{x}:0',
         '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
        stdout=subprocess.PIPE,
    ) as ffmpeg:
        assert ffmpeg.stdout is not None
        while len(frame := ffmpeg.stdout.read(size)) == size:
            rows.append(logo_rows(frame, width))
    if ffmpeg.returncode != 0:
        sys.exit(f'{gif} 를 읽지 못했습니다 (ffmpeg 종료 코드 {ffmpeg.returncode})')
    return rows


def spans(shown: list[bool]) -> list[tuple[int, int]]:
    """로고가 보이는 장면 번호를 (첫 장면, 끝 장면) 구간으로 묶는다."""
    result: list[tuple[int, int]] = []
    for index, visible in enumerate(shown):
        if not visible:
            continue
        if result and result[-1][1] == index - 1:
            result[-1] = (result[-1][0], index)
        else:
            result.append((index, index))
    return result


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit('사용법: python3 scripts/demo/hide_plan.py <GIF 경로>')
    gif = Path(sys.argv[1])
    rows = logo_frames(gif)
    top, bottom = LOGO_ROWS
    moved = [
        (index, found) for index, found in enumerate(rows)
        if found and (abs(found[0] - top) > 1 or abs(found[-1] - bottom) > 1)
    ]
    if moved:
        index, found = moved[0]
        sys.exit(
            f'{index}번째 장면 등 {len(moved)}개 장면에서 로고가 제자리(y {top}~{bottom})가 아닌 '
            f'y {found[0]}~{found[-1]} 에 보입니다. 요금제 글자도 덮는 칸 밖이라 PLAN_BOX·LOGO_ROWS 를 다시 재야 합니다'
        )
    shown = spans([bool(found) for found in rows])
    if not shown:
        sys.exit('배너 로고가 보이는 장면이 없습니다')
    # 장면 번호(n)로 덮는다. 읽을 때와 같은 passthrough 라 번호가 같다
    enable = '+'.join(f'between(n,{first},{last})' for first, last in shown)
    x, y, width, height = PLAN_BOX
    covered = f"drawbox=x={x}:y={y}:w={width}:h={height}:color={BACKGROUND}:t=fill:enable='{enable}'"
    output = gif.with_suffix('.tmp.gif')
    try:
        subprocess.run(
            ['ffmpeg', '-v', 'error', '-y', '-i', str(gif), '-fps_mode', 'passthrough', '-filter_complex',
             f'[0]{covered},split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=none',
             str(output)],
            check=True,
        )
        output.replace(gif)
    finally:
        # 실패하면 덮다 만 GIF 가 저장소에 남지 않게 지운다 (성공하면 이미 옮겨져 없다)
        output.unlink(missing_ok=True)
    print(f'{gif}: 장면 {len(rows)}개 중 {sum(1 for found in rows if found)}개에서 요금제 글자를 덮었습니다 (장면 '
          + ', '.join(f'{first}~{last}' for first, last in shown) + ')')


if __name__ == '__main__':
    main()
