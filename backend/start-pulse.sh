#!/bin/bash
# A PulseAudio server with one null sink, so Chrome sees an audio output device
# like any desktop (enumerateDevices() is empty without one: a server/VM tell).
# Nothing is played anywhere; Chrome connects via PULSE_SERVER (supervisord.conf).
set -e
mkdir -p /tmp/pulse
exec /usr/bin/pulseaudio -n --daemonize=no --use-pid-file=no --exit-idle-time=-1 --disallow-exit \
    --disable-shm=yes --log-target=stderr \
    -L "module-native-protocol-unix socket=/tmp/pulse/native auth-anonymous=1" \
    -L "module-null-sink sink_name=speakers sink_properties=device.description=Speakers" \
    -L "module-always-sink"
