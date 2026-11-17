# Lean 4 + Mathlib sandbox image for the RLM proving mode.
#
# RLMEnv runs the root model's REPL inside this sandbox. Baking Lean + Mathlib in
# (with prebuilt .olean cache) lets the model verify proofs *in-sandbox* via the
# `lean_check` command — no Lean needed on Prime's host/env container. Pinned to the
# exact toolchain + Mathlib revision our env uses so in-sandbox checks match scoring.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
        python3 python3-requests \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

# elan + the pinned Lean toolchain.
ENV ELAN_HOME=/opt/elan
ENV PATH=/opt/elan/bin:$PATH
RUN curl -fsSL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
        -o /tmp/elan-init.sh \
    && sh /tmp/elan-init.sh -y --default-toolchain leanprover/lean4:v4.28.0 \
    && rm /tmp/elan-init.sh

# Mathlib at the revision matching our local env, with prebuilt oleans (no compile).
ARG MATHLIB_REV=v4.28.0
RUN git clone https://github.com/leanprover-community/mathlib4.git /opt/mathlib \
    && cd /opt/mathlib && git checkout ${MATHLIB_REV}
WORKDIR /opt/mathlib
RUN lake exe cache get

# In-sandbox verifier: write a full Lean file and compile it against Mathlib.
COPY lean_check.py /usr/local/bin/lean_check
RUN chmod +x /usr/local/bin/lean_check
ENV LEAN_SANDBOX_PROJECT=/opt/mathlib

# Warm the Mathlib import once so the first real check isn't paying the full cost
# (and so the build fails loudly here if `import Mathlib` is broken).
RUN printf 'import Mathlib\nexample : 1 + 1 = 2 := by norm_num\n' | lean_check

WORKDIR /opt/mathlib
