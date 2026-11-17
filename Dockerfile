# First: docker build -t lean-rlm-sandbox:local sandbox
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM lean-rlm-sandbox:local
COPY --from=uv /uv /uvx /bin/
RUN git clone --depth 1 --branch v4.28.0 https://github.com/leanprover-community/repl.git /opt/repl \
    && cd /opt/repl && lake build
ENV UV_PYTHON_INSTALL_DIR=/opt/python
WORKDIR /app
COPY . /app
RUN uv sync --frozen --no-dev --python 3.12
ENV LEAN_CHECKER_BACKEND=local
ENV LEAN_PROJECT_DIR=/opt/mathlib
ENV LEAN_REPL_CMD=/opt/repl/.lake/build/bin/repl
ENV LEAN_POOL_SIZE=2
ENTRYPOINT ["uv", "run", "--frozen", "--no-dev"]
CMD ["python", "scripts/check_environment.py"]
