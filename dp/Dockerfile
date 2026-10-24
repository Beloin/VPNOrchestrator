# Data plane image: Click modular router (userlevel).
#
# One image, many routers. The orchestrator instantiates one container per
# router and feeds each its own Click config (mounted ConfigMap at
# /etc/click/router.click). Config is runtime data, never baked into the image.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Build deps + runtime debugging tools (ping/tcpdump for verifying forwarding).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        libpcap-dev \
        iproute2 \
        iputils-ping \
        tcpdump \
    && rm -rf /var/lib/apt/lists/*

# Build userlevel Click from source (no kernel module).
RUN git clone --depth 1 https://github.com/kohler/click /opt/click \
    && cd /opt/click \
    && ./configure --disable-linuxmodule \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && rm -rf /opt/click

# Config path is the mount point. Override CMD to point elsewhere.
ENTRYPOINT ["click"]
CMD ["/etc/click/router.click"]
