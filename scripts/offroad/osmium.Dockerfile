# osmium-tool for the offroad-region recipe. Installed inside the image only; nothing goes on the build host.
FROM debian:12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends osmium-tool && rm -rf /var/lib/apt/lists/*
