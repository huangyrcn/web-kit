#!/bin/bash
# Reserved as a future hook; supervisord runs Xvfb directly.
exec /usr/bin/Xvfb :99 -screen 0 1920x1080x24
