# Sandbox for C mutants: the same compiler and libc as GitHub's ubuntu-24.04 runners.
# The network is only for building this image; sandbox.sh runs it with none. test/fw's files need a
# position-independent Python, which Ubuntu's isn't: sandbox.sh mounts CMUT_PYTHON's, read-only.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev python3 ffmpeg lame \
        libavcodec-dev libavutil-dev libmad0-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
