"""README 맨 위 로고(docs/logo.svg)를 만든다.

글자는 외곽선(path)으로 바꿔 넣는다. SVG 안에 글자로 두면 보는 사람 컴퓨터에 그 글꼴이 없을 때 다른 글꼴로 바뀐다.
바탕을 색으로 채워서 GitHub 밝은 화면·어두운 화면과 PyPI 에서 파일 하나로 같게 보인다 (PyPI 는 밝은·어두운 판을
고르는 `<source>` 태그를 걸러 낸다).

글꼴은 저장소에 두지 않는다. Google Fonts 의 IBM Plex Mono SemiBold 와 IBM Plex Sans KR SemiBold(둘 다 OFL)를 받아
경로를 넘긴다.

    uv run --no-project --with fonttools python scripts/logo/build_logo.py IBMPlexMono-SemiBold.ttf IBMPlexSansKR-SemiBold.ttf
"""

import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont


OUTPUT: Path = Path(__file__).resolve().parents[2] / 'docs' / 'logo.svg'
TICKET_COLOR: str = '#2B5CE6'
INK: str = '#FFFFFF'
# 표 윤곽 (360×120): 모서리 반지름 14, 절취선(x 262) 위아래에 반지름 10 의 홈
TICKET: str = 'M18 4H252A10 10 0 0 0 272 4H342A14 14 0 0 1 356 18V102A14 14 0 0 1 342 116H272A10 10 0 0 0 252 116H18A14 14 0 0 1 4 102V18A14 14 0 0 1 18 4Z'
# 오른쪽을 보는 비행기 (24×24, 중심 12,12)
PLANE: str = 'M20 10.8C21.6 10.8 22.6 11.4 22.6 12C22.6 12.6 21.6 13.2 20 13.2L14 13.2L9 20.5L7.2 20.5L10.4 13.2L6 13.2L4.2 16.2L3 16.2L3.8 12.9L3.8 11.1L3 7.8L4.2 7.8L6 10.8L10.4 10.8L7.2 3.5L9 3.5L14 10.8Z'


def _number(value: float) -> str:
    """좌표를 소수 둘째 자리까지, 끝의 0 은 떼고 쓴다."""
    return f'{value:.2f}'.rstrip('0').rstrip('.')


def text_path(font: TTFont, text: str, x: float, baseline: float, size: float, spacing: float = 0) -> str:
    """글자열 하나를 SVG path 의 d 값으로 바꾼다.

    Args:
        font: 글꼴.
        text: 쓸 글자열.
        x: 첫 글자의 왼쪽 x.
        baseline: 기준선 y.
        size: 글자 크기(px).
        spacing: SVG letter-spacing 처럼 글자마다 뒤에 더하는 간격(px). Defaults to 0.

    Returns:
        모든 글자의 외곽선을 이은 d 값.
    """
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = size / font['head'].unitsPerEm
    pen = SVGPathPen(glyph_set, ntos=_number)
    for char in text:
        glyph = glyph_set[cmap[ord(char)]]
        # 글꼴 좌표는 y 가 위로 커지고 SVG 는 아래로 커진다
        glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        x += glyph.width * scale + spacing
    return pen.getCommands()


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit('사용법: build_logo.py <IBMPlexMono-SemiBold.ttf> <IBMPlexSansKR-SemiBold.ttf>')
    mono, sans = (TTFont(path) for path in sys.argv[1:])
    # 배치는 로고를 고를 때 본 미리보기와 같다
    name = text_path(mono, 'baenggipyo', x=28, baseline=63, size=34)
    korean = text_path(sans, '뱅기표', x=29, baseline=91, size=13, spacing=1.2)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="360" height="120" viewBox="0 0 360 120" role="img" aria-label="baenggipyo (뱅기표)">
  <title>baenggipyo (뱅기표)</title>
  <path d="{TICKET}" fill="{TICKET_COLOR}"/>
  <line x1="262" y1="22" x2="262" y2="98" stroke="{INK}" stroke-opacity=".55" stroke-width="2.4" stroke-dasharray="0.1 7" stroke-linecap="round"/>
  <path d="{name}" fill="{INK}"/>
  <path d="{korean}" fill="{INK}" fill-opacity=".8"/>
  <path d="{PLANE}" fill="{INK}" transform="translate(310 60) scale(2.1) translate(-12 -12)"/>
</svg>
"""
    OUTPUT.write_text(svg, encoding='utf-8')
    print(f'{OUTPUT} ({len(svg.encode())} bytes)')


if __name__ == '__main__':
    main()
