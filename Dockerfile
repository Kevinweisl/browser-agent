# browser-agent — Zeabur deploy image
#
# Why the official Playwright image rather than python:3.12-slim?
#   Chromium + its system libs are ~500 MB and notoriously sensitive to
#   library drift. The Playwright maintainers ship a tagged image that
#   pins the right Chromium build for each Playwright release, so we
#   don't run `playwright install chromium` at deploy time.
#
# Why noble (Ubuntu 24.04) instead of jammy (22.04)?
#   pyproject.toml requires `python>=3.12`. Noble ships 3.12 as the
#   default `python3`; jammy ships 3.10 and would force a deadsnakes PPA
#   detour. Noble keeps the Dockerfile boring.
#
# Tag pinned to the same Playwright version we install via pip — see
# `playwright==1.59.0` in pyproject.toml. The two MUST move together; if
# pip installs a newer Playwright Python wrapper than the Chromium build
# baked into this image, BrowserType.launch fails with "Executable doesn't
# exist at /ms-playwright/chromium_headless_shell-XXXX/...".
FROM mcr.microsoft.com/playwright/python:v1.59.0-noble

# Faster, smaller pip; no .pyc clutter; unbuffered logs for `docker logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Ubuntu 24.04 marks the system Python as PEP 668 "externally managed".
# The upstream Playwright Dockerfile already strips that file, but we
# keep this here as a defensive no-op in case the base ever changes.
RUN rm -f /usr/lib/python3.12/EXTERNALLY-MANAGED

# --- Dependency layer (cached as long as pyproject + readme don't change) ---
COPY pyproject.toml README.md LICENSE ./
# Hatchling needs the package source present at install time to build the
# wheel, so we have to copy `src/` before `pip install -e .`. We split the
# rest of the tree into a later layer so eval/UI changes don't bust the
# (slow) dependency layer.
COPY src ./src
RUN pip install --no-cache-dir -e .

# --- Application layer ---
COPY prompts ./prompts
COPY skills ./skills
COPY evals ./evals
COPY scripts ./scripts
COPY ui ./ui
# tests/ and docs/ are intentionally NOT copied — see .dockerignore rationale.
# The official Playwright image already has Chromium installed under
# /ms-playwright; no `playwright install` step needed.

EXPOSE 8000

# Exec-form CMD wrapping `sh -c` so we get both: (a) JSON-form recommended
# by Docker for clean signal handling, and (b) shell variable expansion of
# ${PORT} which Zeabur injects at runtime. `--app-dir src` keeps imports
# like `server.main` resolving correctly even though the package is
# installed editable.
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --app-dir src"]
