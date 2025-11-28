#!/bin/bash

appid="$(/usr/local/sbin/appid.sh "$1")"
appBoxRoot="$(getent passwd "$(whoami)" | cut -f6 -d:)/.local/apprun/boxes/"

if [[ ! -d "$appBoxRoot" ]]; then
    mkdir -p "$appBoxRoot"
fi

if [[ -d "$1" ]]; then
    if [[ ! -f "$1/main.py" ]] && [[ ! -f "$1/main.sh" ]] && [[ ! -f "$1/main.jar" ]] && [[ ! -x "$1/main" ]]; then
        notify-send "Preparation Failed" "No valid entry file found in $1" --icon="/usr/share/AppRun/apprun.png" --app-name="AppRun Framework"
        echo "No main.py, main.sh, main.jar, or executable main file found in $1. Skipping preparation."
        exit 9
    fi
fi

if [[ ! -d "$appBoxRoot/$appid" ]]; then
    echo "Preparing application cache for $appid..."
    mkdir -p "$appBoxRoot/$appid"
fi


if [ -f "$1/main.py" ]; then
    if [ ! -f "$appBoxRoot/$appid/pyvenv/bin/python3" ]; then
        python3 -m venv "$appBoxRoot/$appid/pyvenv"
    fi

    if [ -f "$1/requirements.txt" ]; then

        # Check checksum of requirements.txt
        if [ -f "$appBoxRoot/$appid/requirements.txt.checksum" ]; then
            old_checksum=$(cat "$appBoxRoot/$appid/requirements.txt.checksum")
        else
            old_checksum=""
        fi

        new_checksum=$(md5sum "$1/requirements.txt" | awk '{ print $1 }')

        # Check if app bundle contains AppRunMeta/DesktopLink/Icon.png file. If so, use it as icon for notifications.
        # Otherwise, use /usr/share/AppRun/unknown-app-icon.png
        if [ -f "$1/AppRunMeta/DesktopLink/Icon.png" ]; then
            icon_path="$1/AppRunMeta/DesktopLink/Icon.png"
        else
            icon_path="/usr/share/AppRun/unknown-app-icon.png"
        fi

        if [[ "$old_checksum" == "" ]]; then
            echo "First time setup, installing dependencies..."
            echo "Running preinstallation..."
            notify-send "Installing Dependencies" "Installing dependencies for $appid. This may take a while." --icon="$icon_path" --app-name="AppRun Framework" --expire-time=3
            "$appBoxRoot/$appid/pyvenv/bin/python3" -m pip install --upgrade pip setuptools wheel
            "$appBoxRoot/$appid/pyvenv/bin/python3" -m pip install -r "$1/requirements.txt"
            echo "$new_checksum" > "$appBoxRoot/$appid/requirements.txt.checksum"
            notify-send "Dependencies Installed" "Dependencies for $appid have been installed." --icon="$icon_path" --app-name="AppRun Framework" --expire-time=3
        elif [[ "$old_checksum" != "$new_checksum" ]]; then
            echo "Requirements file changed, reinstalling dependencies..."
            notify-send "Updating Dependencies" "Dependencies for $appid have changed. Reinstalling. This may take a while." --icon="$icon_path" --app-name="AppRun Framework" --expire-time=3
            rm -rf "$appBoxRoot/$appid/pyvenv"
            python3 -m venv "$appBoxRoot/$appid/pyvenv"
            echo "Running preinstallation..."
            "$appBoxRoot/$appid/pyvenv/bin/python3" -m pip install --upgrade pip setuptools wheel
            "$appBoxRoot/$appid/pyvenv/bin/python3" -m pip install -r "$1/requirements.txt"
            echo "$new_checksum" > "$appBoxRoot/$appid/requirements.txt.checksum"
            notify-send "Dependencies Updated" "Dependencies for $appid have been updated." --icon="$icon_path" --app-name="AppRun Framework" --expire-time=3
        fi
    fi
fi

# Check if AppRunMeta/DesktopLink exists.
# If so, copy the content of DesktopLink to ~/.local/share/applications/ where new name is the bundle ID
if [ -f "$1/AppRunMeta/DesktopLink" ]; then
    desktopLinkContent=$(cat "$1/AppRunMeta/DesktopLink")
    desktopDir="$HOME/.local/share/applications"
    if [ ! -d "$desktopDir" ]; then
        mkdir -p "$desktopDir"
    fi
    echo "$desktopLinkContent" > "$desktopDir/$appid.desktop"
    chmod +x "$desktopDir/$appid.desktop"
fi


exit 0
