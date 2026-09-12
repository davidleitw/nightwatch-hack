FROM python:3.12-slim

WORKDIR /app
COPY traffic.py ./traffic.py

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["python", "traffic.py"]
