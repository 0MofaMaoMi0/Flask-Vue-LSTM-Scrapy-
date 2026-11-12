#!/bin/bash
set -euo pipefail
test "$(id -u)" = 0
test "$(cat /sys/class/dmi/id/product_uuid)" = '3f614d56-7436-7a96-c8ea-a786bcc02265'
test ! -e /etc/sudoers.d/93-mi2-m6-operations
install -d -m 700 /etc/movieinsight-m6
install -d -m 755 /opt/movieinsight-m6
install -m 600 -o root -g root /home/zhang/m6-instance.json /etc/movieinsight-m6/instance.json
install -m 644 -o root -g root /home/zhang/m6-operations.py /opt/movieinsight-m6/operations.py
printf '%s\n' '#!/bin/sh' 'exec /usr/bin/python3 /opt/movieinsight-m6/operations.py "$@"' > /usr/local/sbin/mi2-m6-operations
chmod 755 /usr/local/sbin/mi2-m6-operations
chown root:root /usr/local/sbin/mi2-m6-operations
printf '%s\n' 'zhang ALL=(root) NOPASSWD: /usr/local/sbin/mi2-m6-operations deploy, /usr/local/sbin/mi2-m6-operations initialize, /usr/local/sbin/mi2-m6-operations ssh-harden, /usr/local/sbin/mi2-m6-operations migrate, /usr/local/sbin/mi2-m6-operations status, /usr/local/sbin/mi2-m6-operations dependencies, /usr/local/sbin/mi2-m6-operations check, /usr/local/sbin/mi2-m6-operations start-app, /usr/local/sbin/mi2-m6-operations restart-app, /usr/local/sbin/mi2-m6-operations backup, /usr/local/sbin/mi2-m6-operations restore-test' > /etc/sudoers.d/93-mi2-m6-operations
chmod 440 /etc/sudoers.d/93-mi2-m6-operations
visudo -cf /etc/sudoers.d/93-mi2-m6-operations
echo 'Fixed M6 guest operations enabled.'
