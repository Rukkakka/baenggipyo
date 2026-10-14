"""네이버 service: client 호출을 묶어 공통 항공권 모델로 정규화한다.

모듈은 CLI 명령 묶음 하나씩 맡는다 (references/provider-conventions.md 'service 모듈을 나누는 기준',
references/providers/naver.md '명령과 요청 흐름'). import 는 common → airport → 기능 모듈 방향으로만 한다.
"""
