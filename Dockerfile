ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY india /app/india
COPY legacy/trader-scanner/scan.py /app/legacy/trader-scanner/scan.py
COPY tests/fixtures/india/synthetic.csv /app/examples/synthetic.csv
COPY config/paper.example.json /app/config/paper.example.json
COPY config/calendar.2026.json /app/config/calendar.2026.json
COPY LICENSE /app/LICENSE
USER 10001:10001
ENTRYPOINT ["python", "-m", "india"]
CMD ["--help"]
