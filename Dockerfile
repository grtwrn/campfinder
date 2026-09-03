FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# Only what the site needs. The scraping modules (reserveamerica.py, campspot.py,
# newbook.py, browser.py) are deliberately NOT copied: this host never reads a
# booking system, and leaving them out makes that structural rather than a promise.
COPY plan.py wsgi.py ./
COPY web/ ./web/

RUN useradd -m app && mkdir -p /data && chown -R app:app /app /data
USER app

ENV CAMP_DB=/data/camp.db PORT=8080
EXPOSE 8080

# Single worker: SQLite on one volume, and threads handle the concurrency this
# needs. Long timeout because a scrypt hash is deliberately slow.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", \
     "--timeout", "60", "--access-logfile", "-", "wsgi:app"]
