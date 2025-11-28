#!/bin/bash

set -e

ln -s /usr/local/sbin/apprun.sh /usr/local/bin/apprun
ln -s /usr/local/sbin/apprunutil.sh /usr/local/bin/apprunutil
ln -s /usr/local/sbin/appid.sh /usr/local/bin/appid
ln -s /usr/local/sbin/apprun-prepare.sh /usr/local/bin/apprun-prepare
ln -s /usr/local/sbin/dictionary.py /usr/local/bin/dictionary

if [[ -f /tmp/DoNotEnableAppRunDropInService ]]; then
    rm /tmp/DoNotEnableAppRunDropInService
    exit 0
fi


/usr/local/sbin/apprun-prepare.sh "/opt/aisp/services/com.acadia.aisp.services.apprundropin.apprun"
/opt/aisp/sys/sbin/services.sh enable com.acadia.aisp.services.apprundropin
