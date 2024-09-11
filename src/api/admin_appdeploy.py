"""
Functions for optional app deployment feature.

..codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

import os
import pathlib
import re
import shutil
from glob import glob
from tempfile import mkdtemp
from zipfile import ZipFile

from django.conf import settings


def handle_uploaded_app_deploy_file(file, app_title, app_name=None, replace=False):
    """
    Handle uploaded form file `file` which should be a validated ZIP file that contains an R app for deployment.

    :param file: deployment ZIP file
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
            raise FileNotFoundError("required renv.lock file not found")

        # create list of members from the zip file that will be extracted
        ignore_patterns = [
            re.compile(pttrn, re.I)
            for pttrn in (
                r"^\..+",
                r"^renv/",
                r".+\.rproj$",
                r"^makefile$",
                r"^readme.md$",
                "^install.txt$",
                "^restart.txt$",
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
        deploytarget = settings.APPS_DEPLOYMENT["upload_path"] / app_name

        if os.path.exists(deploytarget):
            if replace:
                shutil.rmtree(deploytarget)
            else:
                raise FileExistsError(f'Deployed app already exists at location "{deploytarget}"')

        shutil.move(os.path.join(tmptarget, apppath), deploytarget)

        # trigger (re-)installation of dependencies
        (deploytarget / "install.txt").touch()

        return app_name


def remove_deployed_app(appdir):
    """
    Remove the deployed app from `appdir`.
    """
    if not appdir or re.search("[^a-z0-9_-]", appdir):
        raise ValueError("invalid app path")

    deploytarget = settings.APPS_DEPLOYMENT["upload_path"] / appdir
    if not deploytarget.is_relative_to(settings.APPS_DEPLOYMENT["upload_path"]) or not deploytarget.exists():
        raise ValueError("invalid app path")
    shutil.rmtree(deploytarget)


def get_deployed_app_info(appdir):
    """
    Construct a dict with monitoring information for a deployed app at `appdir`.

    :param appdir: deployed app path
    :return: a dict with keys status, status_class, install_log and error_logs containing the respective information
             for the app
    """
    deploytarget = settings.APPS_DEPLOYMENT["upload_path"] / appdir

    if not deploytarget.exists():
        raise ValueError("invalid app path")

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
        install_log = (deploytarget / "install.log").read_text()
    else:
        install_log = "– install.log file not found –"

    log_path = settings.APPS_DEPLOYMENT.get("log_path", None)
    error_logs = {}
    if log_path:
        for logf in sorted(glob(str(log_path / f"{appdir}-*.log"))):
            logfpath = pathlib.Path(logf)
            error_logs[logfpath.parts[-1]] = logfpath.read_text()

    return {"status": status, "status_class": status_class, "install_log": install_log, "error_logs": error_logs}
