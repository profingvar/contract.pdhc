FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# curl is needed for the HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY app /srv/app

ENV FLASK_APP=app.main:app

EXPOSE 9021

# Container-level liveness. Rollup #350 §1.4-analogue.
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9021/health || exit 1

# Bind to 127.0.0.1 (not 0.0.0.0). docker-compose.yml host-side already
# restricts to 127.0.0.1, but binding on 0.0.0.0 inside the container
# is a layering violation — anyone running the image with `-p 9021:9021`
# (no 127.0.0.1 prefix) exposes it LAN-wide. Rollup #350 §1.1.
# --access-logfile - flows HTTP access lines to `docker logs`.
CMD ["gunicorn", \
     "-w", "2", \
     "-b", "127.0.0.1:9021", \
     "--access-logfile", "-", \
     "--access-logformat", "%(t)s %(h)s \"%(r)s\" %(s)s %(L)ss", \
     "app.main:app"]
