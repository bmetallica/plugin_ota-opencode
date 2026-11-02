# Gedächtnis für opencode — der Dienst.
FROM python:3.13-slim-trixie

# Wochenstempel (Makefile): erneuert die Paket-Upgrade-Schicht spätestens
# wöchentlich, statt sie im Build-Cache einzufrieren.
ARG PAKETSTAND=
RUN apt-get update \
 && apt-get -y upgrade \
 && apt-get install -y --no-install-recommends postgresql-client-17 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gedaechtnis ./gedaechtnis

ARG VERSION=dev
ENV GEDAECHTNIS_VERSION=${VERSION} PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/gesund', timeout=4)"
CMD ["uvicorn", "gedaechtnis.haupt:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--no-server-header"]
