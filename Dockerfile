FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY manage.py entrypoint.sh ./
COPY gluckstal ./gluckstal
COPY shop ./shop
COPY locale ./locale
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput -v0 \
 && useradd -r -u 10001 app && mkdir -p /data && chown app /data && chmod +x entrypoint.sh
USER app
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=4)"
ENTRYPOINT ["./entrypoint.sh"]
