"""한글 이름 뒤 조사 고르기.

`references/code-style.md` §1 의 문구 규칙(끝 글자가 바뀌는 한글 이름 뒤 조사는 이것으로 고른다)이 쓰는 도구다. 지금 문구에는 그런
자리가 없어 저장소 안 호출부가 없지만, 규칙의 일부라 지우지 않는다 (2026-10-11 리뷰에서 확인, 남기기로 결정).
"""


def particle(word: str, pair: str) -> str:
    """`word` 뒤에 붙일 조사를 받침에 맞춰 고른다.

    공항·도시 이름처럼 끝 글자가 바뀌는 한글 이름에 쓴다 ('인천' 과, '오사카' 와). 숫자·코드·옵션 값은
    읽는 법에 따라 받침이 달라(1 → 일, 2 → 이) 고를 수 없으니, 조사가 필요 없게 문장을 바꾼다
    ('허용되지 않는 값입니다: ...').

    Args:
        word: 조사를 붙일 말.
        pair: 받침이 있을 때/없을 때 조사 ('은/는', '이/가', '을/를', '과/와'). '으로/로' 는 ㄹ 받침 예외가 있어
            받지 않는다.

    Returns:
        고른 조사. 한글로 끝나지 않으면 받침을 몰라 '은(는)' 처럼 둘 다 쓴다.
    """
    with_final, without_final = pair.split('/')
    last = word[-1:]
    if not ('가' <= last <= '힣'):
        return f'{with_final}({without_final})'
    return with_final if (ord(last) - ord('가')) % 28 else without_final


def quoted(word: str, pair: str) -> str:
    """따옴표로 감싼 `word` 와 조사 ("'인천' 과", "'오사카' 와")."""
    return f"'{word}' {particle(word, pair)}"
