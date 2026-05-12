# PyPI 배포 (easyobs-agent)

**영문 원문:** [`PUBLISHING.md`](PUBLISHING.md)

## 사전 요구 사항

### 1. 빌드 도구 설치

```bash
pip install build twine
```

### 2. PyPI 계정 설정

1. https://pypi.org/account/register/ 에서 가입
2. 2FA 활성화(필수)
3. API 토큰 생성: Account Settings → API tokens → "Add API token"

### 3. `.pypirc` 인증 설정

샘플 파일을 복사한 뒤 실제 토큰으로 채웁니다.

```bash
# 프로젝트 루트에 .pypirc가 이미 있을 수 있습니다(git에서 제외됨).
# 또는 홈 디렉터리에 두어 전역으로 사용:
cp .pypirc.sample ~/.pypirc
# 그다음 password 값을 실제 토큰으로 바꿉니다.
```

> **참고:** `.pypirc`는 `.gitignore`에 포함되어 커밋되지 않습니다.

---

## 빌드 및 배포

### 1단계: 패키지 빌드

```bash
cd packages/easyobs-agent
python -m build
```

성공 시 `dist/`에 다음이 생깁니다.

- `easyobs_agent-0.1.0-py3-none-any.whl`
- `easyobs_agent-0.1.0.tar.gz`

### 2단계: (권장) TestPyPI에서 검증

```bash
twine upload --repository testpypi dist/*
```

설치 확인:

```bash
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    easyobs-agent
```

### 3단계: PyPI에 업로드

```bash
# 프로젝트 루트의 .pypirc 사용
twine upload --config-file ../../.pypirc dist/*

# 또는 ~/.pypirc가 설정된 경우
twine upload dist/*
```

---

## 버전 올리기

`packages/easyobs-agent/pyproject.toml`의 `version` 필드를 수정한 뒤 다시 빌드하고 업로드합니다.

```bash
# version = "0.2.0" 으로 바꾼 뒤
cd packages/easyobs-agent
rm -rf dist/
python -m build
twine upload dist/*
```

> PyPI는 이미 올라간 동일 버전을 다시 올릴 수 없습니다. 항상 버전 번호를 올리세요.

---

## 최종 사용자 설치

배포 후 누구나 다음으로 설치할 수 있습니다.

```bash
pip install easyobs-agent
```

### requirements.txt

```
easyobs-agent>=0.1.0
```

### pyproject.toml

```toml
[project]
dependencies = [
    "easyobs-agent>=0.1.0",
]
```

---

## 디렉터리 구조

```
packages/easyobs-agent/
├── pyproject.toml          # 빌드 및 메타데이터
├── README.md               # PyPI 프로젝트 페이지에 표시
├── LICENSE                 # MIT
├── PUBLISHING.md           # 이 문서의 영문판
└── src/
    └── easyobs_agent/
        ├── __init__.py     # 공개 API
        ├── boot.py         # init() 진입점
        ├── traced.py       # @traced 데코레이터
        ├── tags.py         # SpanTag + record_* 헬퍼
        ├── span_scope.py   # span_block() 컨텍스트 매니저
        └── callbacks/
            ├── __init__.py
            └── langchain.py  # LangChain 콜백 핸들러
```

---

## CI/CD 자동화 (GitHub Actions)

릴리스 태그 생성 시 자동 배포하려면 `.github/workflows/publish-agent.yml`을 추가합니다.

```yaml
name: Publish easyobs-agent to PyPI

on:
  release:
    types: [published]
  push:
    tags: ["agent-v*"]

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install build tools
        run: pip install build
      - name: Build
        working-directory: packages/easyobs-agent
        run: python -m build
      - name: Publish to PyPI (Trusted Publisher)
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: packages/easyobs-agent/dist/
```

> **Trusted Publisher:** PyPI 프로젝트 Settings → Publishing에서 GitHub 저장소를 등록하면 토큰 없이 자동 배포할 수 있습니다.

---

## 체크리스트

- [ ] 패키지 이름 사용 가능 여부: https://pypi.org/project/easyobs-agent/
- [ ] `python -m build` 성공
- [ ] TestPyPI 업로드 테스트 통과
- [ ] 운영 PyPI 업로드
- [ ] `pip install easyobs-agent` 정상 설치
- [ ] `from easyobs_agent import init, traced` 임포트 성공
