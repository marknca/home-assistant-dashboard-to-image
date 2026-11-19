FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt
COPY renderer.py catalog.py manager.py app.py ./
COPY static ./static

LABEL io.hass.name="Dashboard PNG" \
      io.hass.description="Scheduled HTML dashboard rendering" \
      io.hass.type="addon" \
      io.hass.version="1.1.0"

CMD ["python3", "/app/app.py"]
