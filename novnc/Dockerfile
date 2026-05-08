FROM python:3.13-slim

ARG NOVNC_VERSION=1.7.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir websockify \
    && curl -fsSL "https://github.com/novnc/noVNC/archive/refs/tags/v${NOVNC_VERSION}.tar.gz" \
        | tar -xz -C /opt \
    && mv "/opt/noVNC-${NOVNC_VERSION}" /opt/novnc \
    && printf '<!doctype html><html><head><meta http-equiv="refresh" content="0;url=vnc.html"></head></html>\n' \
        > /opt/novnc/index.html

EXPOSE 6080

CMD ["sh", "-c", "websockify --web=/opt/novnc 0.0.0.0:${NOVNC_LISTEN_PORT:-6080} ${NOVNC_VNC_HOST:-ib-gateway}:${NOVNC_VNC_PORT:-5900}"]