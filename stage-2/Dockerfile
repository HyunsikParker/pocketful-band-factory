FROM python:3.12-slim
WORKDIR /app
ENV PORT=8080 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY engine.py auth.py server.py ./
COPY static ./static
USER 65532:65532
EXPOSE 8080
CMD ["python", "server.py"]
