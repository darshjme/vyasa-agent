FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir '.[admin,messaging]' && useradd --create-home --uid 10000 vyasa && mkdir -p /var/lib/vyasa && chown vyasa:vyasa /var/lib/vyasa
ENV VYASA_HOME=/var/lib/vyasa PYTHONUNBUFFERED=1
USER vyasa
EXPOSE 19000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:19000/healthz', timeout=3)"
CMD ["vyasa", "gateway", "serve", "--bind=0.0.0.0", "--port=19000"]
