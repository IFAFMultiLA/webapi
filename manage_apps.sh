#!/bin/bash

#!/bin/bash

# Check if an argument is given
if [ -z "$1" ]; then
    echo "error: no path provided."
    echo "usage: $0 <path>"
    exit 1
fi

# Assign the first argument to a variable
deploy_path="$1"

# Check if the path exists
if [ ! -d "$deploy_path" ]; then
    echo "error: the path '$deploy_path' does not exist."
    exit 1
fi

echo "searching for apps in '$deploy_path' that require installing dependencies ..."

# Iterate through all folders in the given path and print the folder names
while IFS= read -r -d '' app; do
    if [ -f "$app/install.txt" ] && [ -f "$app/renv.lock" ] ; then
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
    fi
done < <(find "$deploy_path" -mindepth 1 -maxdepth 1 -type d -print0)

echo "done."
