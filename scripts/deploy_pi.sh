#!/bin/sh
# Ship the code to the Raspberry Pi and (re)start it there in Docker.
#
#     scripts/deploy_pi.sh                 # code + .env.prod, rebuild, restart
#
# The Pi runs a 32-bit OS on a 64-bit kernel: Playwright needs arm64, which the kernel can
# run, but the 32-bit Docker's seccomp filter kills arm64 syscalls. So containers run with
# seccomp off, and because `docker build` can't turn it off, the image is built by
# installing into a container and committing it.
#
# The Pi keeps its own data/ (database, browser profile): this never overwrites it.
# Both containers use the host network, so the server stays on the Pi's 127.0.0.1:8765
# exactly as when run locally, and ngrok forwards the public URL to it.
# Web chat from your computer:  ssh -L 8765:localhost:8765 $PI  then open http://localhost:8765
set -eu

PI=${PI:?set PI=user@host of your Raspberry Pi, e.g. PI=pi@raspberrypi.local}
# Its Playwright version must match the one pip installs, or its browsers won't be found.
BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.64.1-noble-arm64
NGROK_IMAGE=ngrok/ngrok:3.39.11-alpine-arm64
ARM64="--platform linux/arm64 --security-opt seccomp=unconfined"
cd "$(dirname "$0")/.."

rsync -az --delete --exclude .venv --exclude data --exclude '.env*' --exclude ngrok.env --exclude __pycache__ --exclude .DS_Store \
    ./ "$PI:grocerybot/"
rsync -az .env.prod "$PI:grocerybot/.env.prod"

ssh "$PI" sh -s <<EOF
set -eu
cd ~/grocerybot
mkdir -p data
docker rm -f grocerybot-build 2>/dev/null || true
docker run --name grocerybot-build $ARM64 -v "\$PWD:/src:ro" $BASE_IMAGE sh -c \
    'pip install --no-cache-dir -q -r /src/requirements.txt playwright==1.64.1 && mkdir -p /app && cp -r /src/grocerybot /src/scripts /src/static /app/'
docker commit -c 'WORKDIR /app' -c 'ENV GROCERYBOT_ENV_FILE=.env.prod' -c 'ENV PYTHONUNBUFFERED=1' \
    -c 'CMD ["uvicorn", "grocerybot.server:app", "--host", "127.0.0.1", "--port", "8765"]' grocerybot-build grocerybot >/dev/null
docker rm grocerybot-build >/dev/null
[ "${BUILD_ONLY:-}" = 1 ] && exit 0

docker rm -f grocerybot 2>/dev/null || true
docker run -d --name grocerybot --restart unless-stopped $ARM64 --network host --init --ipc host \
    -v "\$PWD/data:/app/data" -v "\$PWD/.env.prod:/app/.env.prod:ro" grocerybot
if ! docker ps --format '{{.Names}}' | grep -qx ngrok; then
    domain=\$(sed -n 's|^PUBLIC_BASE_URL=https://\([^ #]*\).*|\1|p' .env.prod)
    docker rm -f ngrok 2>/dev/null || true
    docker run -d --name ngrok --restart unless-stopped $ARM64 --network host --env-file ngrok.env \
        $NGROK_IMAGE http --url="\$domain" 8765
fi
docker image prune -f >/dev/null
docker ps --format '{{.Names}}: {{.Status}}'
EOF
