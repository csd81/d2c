# Release process

d2c uses a single source of truth for the version: `d2c.__version__` in
`src/d2c/__init__.py` (pyproject reads it via `[tool.setuptools.dynamic]`).

## 1. Bump the version

Edit `src/d2c/__init__.py`:

```python
__version__ = "0.2.0"
```

Update `CHANGELOG.md` (move items from **Unreleased** to a new dated section).

## 2. Pre-release checks

```bash
git status --short                       # only intended changes
python -m d2c --version                  # matches the new version
python -m d2c --doctor                   # no FAILs on a clean setup

ruff check .
ruff format --check .
mypy                                     # all of src/d2c
bandit -c pyproject.toml -r src/d2c
pip-audit                                # advisory
pytest -q
```

## 3. Build and verify artifacts

```bash
rm -rf dist build src/d2c.egg-info
python -m build
python -m zipfile -l dist/*.whl          # includes d2c/skills/*.md, no caches/secrets
python -m tarfile -l dist/*.tar.gz
twine check dist/*                        # metadata + long-description render
```

Confirm the wheel `METADATA` `Version:` equals `d2c.__version__`.

## 4. Fresh-venv smoke of the built wheel

```bash
python -m venv /tmp/d2c-release-venv
source /tmp/d2c-release-venv/bin/activate
pip install dist/*.whl
python -m d2c --version
python -m d2c --doctor
deactivate
```

## 5. Tag and push

```bash
git commit -am "Release 0.2.0"
git tag -a v0.2.0 -m "d2c 0.2.0"
git push origin master --tags
```

Pushing a `v*` tag triggers `.github/workflows/release.yml`, which re-runs the
gates, builds the artifacts, runs `twine check`, and uploads them as workflow
artifacts.

## 6. (Optional) Publish

Publishing to PyPI is **not** automatic. When you decide to publish:

```bash
# dry run to TestPyPI first
twine upload --repository testpypi dist/*
# then, deliberately, to PyPI
twine upload dist/*
```

Prefer PyPI Trusted Publishing (OIDC) or a scoped API token stored as a repo
secret over long-lived credentials.
