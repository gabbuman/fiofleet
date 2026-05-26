# A stand-in "device" for exercising fiofleet end-to-end: a generic Linux box
# running the Foundries fioup client, plus WireGuard + sshd so the Factory VPN
# server can reach it. Not a flashed LmP image — the lightweight, real-registration
# path for a fioup/arm64-linux style factory.
FROM debian:trixie

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg \
      openssh-server wireguard-tools iproute2 \
      python3 python3-requests procps iputils-ping jq \
 && rm -rf /var/lib/apt/lists/*

# fioup (pulls docker.io as a dependency; we don't run dockerd in this test)
RUN curl -fsSL https://fioup.foundries.io/pkg/deb/dists/stable/Release.gpg \
      | gpg --dearmor -o /etc/apt/trusted.gpg.d/fioup-stable.gpg \
 && echo "deb [signed-by=/etc/apt/trusted.gpg.d/fioup-stable.gpg] https://fioup.foundries.io/pkg/deb stable main" \
      > /etc/apt/sources.list.d/fioup.list \
 && apt-get update -qq \
 && apt-get install -y --no-install-recommends fioup \
 && rm -rf /var/lib/apt/lists/*

# The fio user the Factory VPN server SSHes in as.
RUN useradd -m -s /bin/bash fio \
 && mkdir -p /home/fio/.ssh /run/sshd \
 && chmod 700 /home/fio/.ssh \
 && chown -R fio:fio /home/fio/.ssh

COPY device-entrypoint.sh /usr/local/bin/device-entrypoint.sh
COPY wg-agent.py /usr/local/bin/wg-agent.py
RUN chmod +x /usr/local/bin/device-entrypoint.sh /usr/local/bin/wg-agent.py

ENTRYPOINT ["/usr/local/bin/device-entrypoint.sh"]
