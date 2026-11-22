FROM python:3.13-alpine
WORKDIR /app
COPY llm.py app.py check.py ./
USER 65532:65532
ENV PYTHONUNBUFFERED=1
CMD ["python", "app.py"]
