FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY www ./www
COPY custom_components ./custom_components
# HA supplies /data/options.json in the add-on's protected data directory.
# Keep the default user so that the entry point can read Supervisor-owned options.
CMD ["python", "-m", "app"]
