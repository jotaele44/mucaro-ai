ARG BASE_IMAGE
FROM ${BASE_IMAGE}

WORKDIR /opt/mucaro-build
COPY pyproject.toml ./
COPY backend ./backend

RUN python -m pip install --no-cache-dir ".[dev]" ruff mypy build \
    && mkdir -p /wheelhouse \
    && python -m pip download --no-cache-dir "setuptools>=69" wheel -d /wheelhouse \
    && rm -rf /opt/mucaro-build

ENV PIP_NO_INDEX=1
ENV PIP_FIND_LINKS=/wheelhouse
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /workspace/repo
