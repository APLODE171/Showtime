FROM python:3.12-slim
WORKDIR /app
COPY server.py ./
COPY static ./static
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
ENV DATA_DIR=/data PORT=8000
EXPOSE 8000
CMD ["python", "server.py"]
