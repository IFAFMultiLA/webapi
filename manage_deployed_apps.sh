#!/bin/bash

# Example bash script for managing apps that were uploaded via the admin interface.
#
# This script could be run on a server e.g. as an inotify-based service that checks for changes in the app deployement
# directory or as a cronjob.
#
# Author: Markus Konrad <markus.konrad@htw-berlin.de>

# set the user and group ownership for each app
target_usr="multila"
target_grp="multila"
# instead of deleting
graveyard_path="app_graveyard"

if [ -n "$graveyard_path" ] && [ ! -d "$graveyard_path" ] ; then
    echo "error: graveyard_path is set to '$graveyard_path' but this directory doesn't exist."
    exit 1
fi

# Check if an argument is given
if [ -z "$1" ]; then
    echo "error: no path provided."
    echo "usage: $0 <path>"
    exit 1
fi

deploy_path="$1"

# Check if the path exists
if [ ! -d "$deploy_path" ]; then
    echo "error: the path '$deploy_path' does not exist."
    exit 1
fi

echo "searching for apps in '$deploy_path' that require installing dependencies ..."

# Iterate through all folders in the given path and print the folder names
while IFS= read -r -d '' app; do
    if [ -f "$app/remove.txt" ] ; then
        if [ -z "$graveyard_path" ] ; then
            echo "> removing app '$(basename "$app")' ..."
            rm -r "$app"
        else
            echo "> moving app '$(basename "$app")' to graveyard ..."
            mv "$app" "$graveyard_path/$app-`date -I`"
        fi
    elif [ -f "$app/install.txt" ] && [ -f "$app/renv.lock" ] ; then
        echo "> installing dependencies for '$(basename "$app")' ..."
        cd $app || continue
        R -e "renv::restore()" > install.log 2>&1
        if [ $? -eq 0 ] ; then
            rm install.txt
            rm -f install_error.txt
            touch restart.txt
            echo ">> done."
        else
            touch install_error.txt
            echo ">> installing dependencies failed. check $app/install.log file."
        fi

        chown -R $target_usr:$target_grp "$app"
    fi
done < <(find "$deploy_path" -mindepth 1 -maxdepth 1 -type d -print0)

echo "done."

