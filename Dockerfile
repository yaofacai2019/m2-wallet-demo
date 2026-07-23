FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 M2_WALLET_HOST=0.0.0.0
WORKDIR /app

RUN useradd --create-home --uid 10001 m2wallet
COPY --chown=m2wallet:m2wallet backend ./backend
COPY --chown=m2wallet:m2wallet prototype ./prototype
COPY --chown=m2wallet:m2wallet README.md ./README.md
RUN mkdir -p /app/data && chown m2wallet:m2wallet /app/data

USER m2wallet
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/v1/health', timeout=2)"]
CMD ["python", "-m", "backend.server"]
