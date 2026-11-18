#!/bin/sh
# Start the local setup window with this checkout's isolated Python environment.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
for environment in .venv-gui .venv-dev .venv; do
    python_path="$project_dir/$environment/bin/python"
    if [ -x "$python_path" ] && "$python_path" -c 'import tkinter' >/dev/null 2>&1; then
        exec "$python_path" -m bambu_monitor setup "$@"
    fi
done
printf '%s\n' '没有找到带 Tk 的项目 Python 环境。请参阅 docs/SETUP-WIZARD.md 安装说明。'
exit 2
