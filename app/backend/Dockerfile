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

# Bind 0.0.0.0 INSIDE the container: docker port-publishing and the
# web->api hop both dial the container's bridge IP, so a loopback bind
# makes the API unreachable (published ports and `api:9021` alike).
# LAN exposure is prevented by the `127.0.0.1:` host-port prefix in
# docker-compose.yml (ticket #72), not by the in-container bind.
# --access-logfile - flows HTTP access lines to `docker logs`.
CMD ["gunicorn", \
     "-w", "2", \
     "-b", "0.0.0.0:9021", \
     "--access-logfile", "-", \
     "--access-logformat", "%(t)s %(h)s \"%(r)s\" %(s)s %(L)ss", \
     "app.main:app"]
