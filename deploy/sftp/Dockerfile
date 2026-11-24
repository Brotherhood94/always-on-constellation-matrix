FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* \
    && groupadd -g 991 desktop-share \
    && useradd -u 991 -g 991 -d /desktop -s /usr/sbin/nologin desktop-share \
    && passwd -d desktop-share \
    && mkdir -p /run/sshd /chroot/desktop
ENTRYPOINT ["/usr/sbin/sshd", "-De", "-f", "/config/sshd_config"]
