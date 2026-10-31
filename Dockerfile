# syntax=docker/dockerfile:1
# One-shot sweep image. Debian ships no vobsub2srt, so VobSub-only files are skipped.
FROM python:3.12-slim-trixie@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

COPY --from=ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 /uv /bin/uv

# opencv-python (a pgsrip dependency) needs libGL and GLib at import time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      mkvtoolnix tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0t64 \
 && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

ENV PATH=/app/.venv/bin:$PATH
# Pass --user with the UID:GID that owns the library, so the sidecars get the right owner.
USER 65534:65534
ENTRYPOINT ["subtrans-sweep"]
