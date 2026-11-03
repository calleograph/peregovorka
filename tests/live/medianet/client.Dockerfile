FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends iproute2 iptables >/dev/null && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -q livekit==1.1.21 livekit-api==1.2.2
