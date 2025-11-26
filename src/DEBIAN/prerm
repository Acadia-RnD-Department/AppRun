#!/bin/bash

if [ -L /usr/local/bin/apprun ]; then
    rm /usr/local/bin/apprun
fi

if [ -L /usr/local/bin/appid ]; then
    rm /usr/local/bin/appid
fi

if [ -L /usr/local/bin/apprun-prepare ]; then
    rm /usr/local/bin/apprun-prepare
fi

if [ -L /usr/local/bin/apprunutil ]; then
    rm /usr/local/bin/apprunutil
fi

if [ -L /usr/local/bin/dictionary ]; then
    rm /usr/local/bin/dictionary
fi

systemctl stop com.acadia.aisp.services.apprundropin.service
systemctl disable com.acadia.aisp.services.apprundropin.service