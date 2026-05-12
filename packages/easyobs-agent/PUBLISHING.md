# Publishing to PyPI (easyobs-agent)

**Korean:** [`PUBLISHING.ko.md`](PUBLISHING.ko.md)

## Prerequisites

### 1. Install Build Tools

```bash
pip install build twine
```

### 2. PyPI Account Setup

1. Register at https://pypi.org/account/register/
2. Enable 2FA (required)
3. Generate API token: Account Settings → API tokens → "Add API token"

### 3. Configure `.pypirc` Authentication

Copy the sample file and fill in your actual token:

```bash
# A .pypirc already exists in the project root (excluded from git).
# Alternatively, place it in your home directory for global use:
cp .pypirc.sample ~/.pypirc
# Then replace the password value with your actual token.
```

> **Note**: `.pypirc` is listed in `.gitignore` and will never be committed.

---

## Build & Publish

### Step 1: Build the Package

```bash
cd packages/easyobs-agent
python -m build
```

On success, `dist/` will contain:
- `easyobs_agent-0.1.0-py3-none-any.whl`
- `easyobs_agent-0.1.0.tar.gz`

### Step 2: (Recommended) Test on TestPyPI

```bash
twine upload --repository testpypi dist/*
```

Verify installation:

```bash
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    easyobs-agent
```

### Step 3: Upload to PyPI

```bash
# Using the project-root .pypirc
twine upload --config-file ../../.pypirc dist/*

# Or if ~/.pypirc is configured
twine upload dist/*
```

---

## Version Bumps

Update the `version` field in `packages/easyobs-agent/pyproject.toml`, then rebuild and upload:

```bash
# After changing version = "0.2.0"
cd packages/easyobs-agent
rm -rf dist/
python -m build
twine upload dist/*
```

> PyPI does not allow re-uploading an existing version. Always increment the version number.

---

## End-User Installation

Once published, anyone can install:

```bash
pip install easyobs-agent
```

### In requirements.txt

```
easyobs-agent>=0.1.0
```

### In pyproject.toml

```toml
[project]
dependencies = [
    "easyobs-agent>=0.1.0",
]
```

---

## Directory Layout

```
packages/easyobs-agent/
├── pyproject.toml          # Build & metadata config
├── README.md               # Displayed on PyPI project page
├── LICENSE                 # MIT
├── PUBLISHING.md           # This file
└── src/
    └── easyobs_agent/
        ├── __init__.py     # Public API
        ├── boot.py         # init() entry point
        ├── traced.py       # @traced decorator
        ├── tags.py         # SpanTag + record_* helpers
        ├── span_scope.py   # span_block() context manager
        └── callbacks/
            ├── __init__.py
            └── langchain.py  # LangChain callback handler
```

---

## CI/CD Automation (GitHub Actions)

To auto-publish on release tag creation, add `.github/workflows/publish-agent.yml`:

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

> **Trusted Publisher**: Register your GitHub repo under PyPI project Settings → Publishing for token-free automated deployments.

---

## Checklist

- [ ] Verify package name availability: https://pypi.org/project/easyobs-agent/
- [ ] `python -m build` succeeds
- [ ] TestPyPI upload test passes
- [ ] Production PyPI upload
- [ ] `pip install easyobs-agent` installs correctly
- [ ] `from easyobs_agent import init, traced` imports successfully
