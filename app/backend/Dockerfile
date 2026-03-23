FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY app /srv/app

ENV FLASK_APP=app.main:app

EXPOSE 9021

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:9021", "app.main:app"]
