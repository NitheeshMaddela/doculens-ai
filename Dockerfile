FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.9.11
WORKDIR /srv
ENV UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY app app
COPY alembic.ini .
COPY migrations migrations
COPY reports reports
COPY sample_data sample_data
RUN useradd --uid 10001 --create-home doculens && mkdir -p /data && chown doculens /data
USER doculens
ENV PATH="/srv/.venv/bin:$PATH"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
