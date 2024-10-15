"""
Functions for optional app deployment feature.

..codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

import os
import pathlib
import re
import shutil
from datetime import datetime
from glob import glob
from tempfile import mkdtemp
from zipfile import ZipFile

from django.conf import settings


def handle_uploaded_app_deploy_file(file, app_title, app_name=None, replace=False):
    """
    Handle uploaded form file `file` which should be a validated ZIP file that contains an R app for deployment.

    :param file: path to deployment ZIP file
    :param app_title: app title
    :param app_name: use this URL-safe app name if given, else derive it from `app_title`
    :param replace: if True, there should already exist a deployed app which should be replaced (i.e. updated)
    :return: URL safe app name derived from `app_title` or as given by `app_name`
    """
    with ZipFile(file, "r") as z:
        # find required "renv.lock" -> this marks the project directory
        apppath = None
        for zpath in z.namelist():
            if os.path.basename(zpath) == "renv.lock":
                apppath = os.path.dirname(zpath)
                break

        if apppath is None:
            raise FileNotFoundError("Required renv.lock file not found.")

        # create list of members from the zip file that will be extracted
        ignore_patterns = [
            re.compile(pttrn, re.I)
            for pttrn in (
                r"^\..+",
                r"^\.git.*",
                r"^renv/",
                r".+\.rproj$",
                r"^makefile$",
                r"^readme.md$",
                "^install.txt$",
                "^restart.txt$",
                "^remove.txt$",
            )
        ]
        members = []
        for zpath in z.namelist():
            if zpath.startswith(apppath):
                if apppath:
                    relpath = zpath[len(apppath) + 1 :]
                else:
                    relpath = zpath

                if all(pttrn.search(relpath) is None for pttrn in ignore_patterns):
                    members.append(os.path.join(apppath, relpath))

        # extract the selected members to a temp. location
        tmptarget = mkdtemp("_new_app")
        z.extractall(tmptarget, members)

        # move the deployment files from the temp. location to the final location
        if not app_name:
            app_name = re.sub("[^a-z0-9_-]", "", app_title.lower().replace(" ", "_"))
        logdir_name = _setting_log_path().name
        if _setting_log_path().parent == _setting_upload_path() and app_name == logdir_name:
            raise ValueError(
                f"The app directory cannot be named '{logdir_name}' (this is the name of the " f"log files directory)."
            )
        deploytarget = _setting_upload_path() / app_name

        if os.path.exists(deploytarget):
            if replace:
                now = datetime.now().isoformat().replace(":", "-")
                oldtarget = _setting_upload_path() / f"{app_name}~old-{now}"
                shutil.move(deploytarget, oldtarget)
                (oldtarget / "remove.txt").touch()
            else:
                raise FileExistsError(f'Deployed app already exists at location "{deploytarget}"')

        shutil.move(os.path.join(tmptarget, apppath), deploytarget)

        # trigger (re-)installation of dependencies
        (deploytarget / "install.txt").touch()
        _trigger_update()

        return app_name


def remove_deployed_app(appdir):
    """
    Remove the deployed app from `appdir`.
    """
    if not appdir or re.search("[^a-z0-9_-]", appdir):
        raise ValueError("Invalid application path.")

    mode = settings.APPS_DEPLOYMENT.get("remove_mode", None)
    deploytarget = _setting_upload_path() / appdir
    if (
        not deploytarget.is_relative_to(_setting_upload_path())
        or not deploytarget.exists()
        or deploytarget == _setting_log_path()
    ):
        raise ValueError("Invalid application path.")

    if mode == "delete":
        shutil.rmtree(deploytarget)
    elif mode == "remove.txt":
        (deploytarget / "remove.txt").touch()

    _trigger_update()


def get_deployed_app_info(appdir):
    """
    Construct a dict with monitoring information for a deployed app at `appdir`.

    :param appdir: deployed app directory
    :return: a dict with keys status, status_class, install_log and error_logs containing the respective information
             for the app
    """
    deploytarget = _setting_upload_path() / appdir

    if not deploytarget.exists() or (deploytarget / "remove.txt").exists():
        raise ValueError("Invalid app directory.")

    if (deploytarget / "install.txt").is_file():
        if (deploytarget / "install_error.txt").is_file():
            status = "installation error"
            status_class = "error"
        elif (deploytarget / "install.log").is_file():
            status = "installing"
            status_class = "warning"
        else:
            status = "installation scheduled"
            status_class = "warning"
    elif (deploytarget / "restart.txt").is_file():
        status = "deployed"
        status_class = "success"
    else:
        status = "unknown"
        status_class = "warning"

    if (deploytarget / "install.log").is_file():
        try:
            install_log = (deploytarget / "install.log").read_text()
        except Exception as err:
            install_log = f"– an error occurred when trying to read the install.log file: {err} –"
    else:
        install_log = "– install.log file not found –"

    error_logs = {}
    if _setting_log_path():
        for logf in sorted(glob(str(_setting_log_path() / f"{appdir}-*.log"))):
            logfpath = pathlib.Path(logf)
            logfbasename = logfpath.parts[-1]
            try:
                error_logs[logfbasename] = logfpath.read_text()
            except Exception as err:
                error_logs[logfbasename] = f"– an error occurred when trying to read the error log file: {err} –"

    return {"status": status, "status_class": status_class, "install_log": install_log, "error_logs": error_logs}


def _trigger_update():
    """Helper file to update a trigger file if the option is enabled in the settings."""
    trigger = settings.APPS_DEPLOYMENT.get("update_trigger_file", None)
    if trigger:
        pathlib.Path(trigger).touch()


def _setting_upload_path():
    """Helper function to get upload path."""
    return pathlib.Path(settings.APPS_DEPLOYMENT["upload_path"])


def _setting_log_path():
    """Helper function to get app log path."""
    if settings.APPS_DEPLOYMENT.get("log_path"):
        return pathlib.Path(settings.APPS_DEPLOYMENT["log_path"])
    else:
        return None
