# lookout in a container, with Chromium and its system libraries preinstalled.
#
#   docker build -t lookout .
#   docker run --rm -v "$PWD/data:/data" --env-file .env lookout check
#
# Mount a directory at /data holding watchlist.yaml; lookout.db is created
# there. That directory must be writable by uid 1000 (see USER below).

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    LOOKOUT_DB=/data/lookout.db \
    LOOKOUT_WATCHLIST=/data/watchlist.yaml

WORKDIR /app
COPY pyproject.toml requirements.txt README.md ./
COPY lookout/ lookout/
COPY tests/ tests/

# `playwright install --with-deps` pulls the apt packages Playwright itself
# declares for this exact version, so the browser and the library can't drift
# apart the way a pinned base image lets them.
# The suite runs here too: a red build produces no image.
RUN pip install --no-cache-dir ".[dev]" \
 && playwright install --with-deps chromium \
 && pytest -q \
 && rm -rf /root/.cache /var/lib/apt/lists/*

# Chromium refuses to start as root unless it is given --no-sandbox, and
# lookout does not pass that flag. Run as an ordinary user rather than
# switching the browser sandbox off.
RUN useradd --create-home --uid 1000 lookout \
 && chown -R lookout:lookout /opt/playwright
USER lookout

# Config and state live here: watchlist.yaml, .env, lookout.db.
WORKDIR /data
VOLUME ["/data"]

# Set TZ (e.g. -e TZ=Europe/Berlin) so scheduled polls line up with your clock.
ENTRYPOINT ["lookout"]
CMD ["run"]
