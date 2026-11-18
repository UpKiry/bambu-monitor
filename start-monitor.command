#!/bin/sh
# Start the native tray and loopback settings UI in a detached project process.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
for environment in .venv-dev .venv-gui .venv; do
    python_path="$project_dir/$environment/bin/python"
    if [ -x "$python_path" ] && "$python_path" -c 'from bambu_monitor.macos_service import check_tray_available; check_tray_available(); import paho.mqtt.client, keyring' >/dev/null 2>&1; then
        exec "$python_path" -m bambu_monitor service --background "$@"
    fi
done
printf '%s\n' '没有找到已安装 cloud,tray 依赖的项目 Python 环境。请参阅 docs/LOCAL-SERVICE.md。'
exit 2
