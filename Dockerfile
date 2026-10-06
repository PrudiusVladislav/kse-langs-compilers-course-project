FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        llvm \
        clang \
        python3 \
        python3-venv \
        python3-pip \
        binutils \
        file \
        make \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/lcd
ENV PATH="/opt/lcd/bin:$PATH"
RUN pip install --no-cache-dir 'llvmlite==0.49.*'

WORKDIR /work
CMD ["bash"]
