FROM python:3.11-slim

RUN pip install --no-cache-dir docker==7.1.0 prometheus_client==0.21.1

WORKDIR /app
COPY exporter.py .

ENV COMPOSE_PROJECT=newlevelhub-backend
ENV EXPORTER_PORT=9101
ENV SCRAPE_INTERVAL=15

EXPOSE 9101
CMD ["python", "exporter.py"]
