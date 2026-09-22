# 1. Start with a lightweight Linux Python image
FROM python:3.12-slim

# 2. Stop Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Set the working directory inside the Linux container
WORKDIR /app

# 4. Install any necessary system-level Linux dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# 5. Grab the latest compiled 'uv' binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 6. Copy ONLY your project config files first (this caches your dependencies!)
COPY pyproject.toml uv.lock README.md ./

# 7. Use uv to sync your dependencies (this uses your lockfile for exact versions)
RUN uv sync --python 3.12 --frozen --no-cache

# 8. Copy the rest of your project files into the container
COPY . .

# 9. Start your bot using 'uv run' so it uses the environment it just built
CMD ["uv", "run", "auric", "start"]