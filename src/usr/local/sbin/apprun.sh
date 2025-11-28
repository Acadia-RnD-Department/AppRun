#!/bin/bash

if [[ -z "$1" ]]; then
    echo "Usage: apprun.sh <AppRun bundle path> [args...]"
    exit 1
fi

/usr/local/sbin/apprun-prepare.sh "$1"
if [ $? -ne 0 ]; then
    exit $?
fi

cmd="$1"
shift

# Get current time to check the application run duration later
start_time=$(date +%s)
exit_code=9

if [ -f "$cmd/main.py" ]; then
    BOX_PATH="$(getent passwd "$(whoami)" | cut -f6 -d:)/.local/apprun/boxes"
    mkdir -p "$BOX_PATH/pycache"
    export PYTHONPYCACHEPREFIX="$BOX_PATH/pycache"
    if [[ -f "$cmd/libs" ]]; then
        export PYTHONPATH="$(/usr/bin/python3 /usr/local/sbin/dictionary.py --dict-collection=apprun-python --string="$(cat "$cmd/libs")"):$PYTHONPATH" 
    elif [[ -f "$cmd/AppRunMeta/libs" ]]; then
        export PYTHONPATH="$(/usr/bin/python3 /usr/local/sbin/dictionary.py --dict-collection=apprun-python --string="$(cat "$cmd/AppRunMeta/libs")"):$PYTHONPATH" 
    fi
    if [[ -f "$cmd/AppRunMeta/EnforceRootLaunch" ]]; then
        if [[ -f "$cmd/AppRunMeta/KeepEnvironment" ]]; then
            sudo -E "$BOX_PATH/$(/usr/local/sbin/appid.sh "$cmd")/pyvenv/bin/python3" "$cmd/main.py" "$@"
            exit_code=$?
        else
            sudo "$BOX_PATH/$(/usr/local/sbin/appid.sh "$cmd")/pyvenv/bin/python3" "$cmd/main.py" "$@"
            exit_code=$?
        fi
    else
        "$BOX_PATH/$(/usr/local/sbin/appid.sh "$cmd")/pyvenv/bin/python3" "$cmd/main.py" "$@"
        exit_code=$?
    fi
elif [ -f "$cmd/main.jar" ]; then
    if [[ -f "$cmd/AppRunMeta/EnforceRootLaunch" ]]; then
        if [[ -f "$cmd/AppRunMeta/KeepEnvironment" ]]; then
            sudo -E java -jar "$cmd/main.jar" "$@"
            exit_code=$?
        else
            sudo java -jar "$cmd/main.jar" "$@"
            exit_code=$?
        fi
    else
        java -jar "$cmd/main.jar" "$@"
        exit_code=$?
    fi
elif [ -f "$cmd/main.sh" ]; then
    if [[ -f "$cmd/AppRunMeta/EnforceRootLaunch" ]]; then
        if [[ -f "$cmd/AppRunMeta/KeepEnvironment" ]]; then
            sudo -E bash "$cmd/main.sh" "$@"
            exit_code=$?
        else
            sudo bash "$cmd/main.sh" "$@"
            exit_code=$?
        fi
    else
        bash "$cmd/main.sh" "$@"
        exit_code=$?
    fi
elif [ -x "$cmd/main" ]; then
    if [[ -f "$cmd/AppRunMeta/EnforceRootLaunch" ]]; then
        if [[ -f "$cmd/AppRunMeta/KeepEnvironment" ]]; then
            sudo -E "$cmd/main" "$@"
            exit_code=$?
        else
            sudo "$cmd/main" "$@"
            exit_code=$?
        fi
    else
        "$cmd/main" "$@"
        exit_code=$?
    fi
else
    echo "No valid main file found to execute at $cmd."
    exit 10
fi

# Get end time and calculate duration
end_time=$(date +%s)
duration=$((end_time - start_time))

# If bundle type is application, do time based crash detection
if [[ "$(/usr/local/sbin/apprunutil.sh GetProperty "$cmd" "DesktopLink/Type")" != "Application" ]]; then
    exit 0
fi

# Check if argument has "--AppRunParam:EnableNoCrashDetectionForShortRunning" to disable crash detection for short running apps
nocheck_short_run="false"
for arg in "$@"; do
    if [[ "$arg" == "--AppRunParam:EnableNoCrashDetectionForShortRunning" ]]; then
        nocheck_short_run="true"
        break
    fi
done

# If duration is less than 1 second, assume a crash and prompt graphical message
if [[ $duration -lt 1 ]] || [[ $exit_code -ne 0 ]]; then
    message="The application terminated too quickly, which may indicate a crash immediately after launch. Please check the application logs or run the application in a terminal for more details."

    if [[ $exit_code -ne 0 ]]; then
        message="The application has exited with a non-zero exit code ($exit_code). Please check the application logs, or run the application in a terminal for more details."
    elif [[ "$nocheck_short_run" == "true" ]]; then
        # If no crash detection for short running apps is enabled, do not show any message
        exit 0
    fi

    echo "AppRun: $message"

    if command -v zenity >/dev/null 2>&1; then
        zenity --error --text="$message" --title="AppRun Application Crash"
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --error --text="$message" --title="AppRun Application Crash"
    fi
fi