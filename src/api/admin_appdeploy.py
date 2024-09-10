"""
Functions for optional app deployment feature.

..codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

import os
import pathlib
import re
import shutil
from tempfile import mkdtemp
from zipfile import ZipFile

from django.conf import settings


def handle_uploaded_app_deploy_file(file, app_title, replace=False):
    """
    Handle uploaded form file `file` which should be a validated ZIP file that contains an R app for deployment.

    :param file: deployment ZIP file
    :param app_title: app title
    :param replace: if True, there should already exist a deployed app which should be replaced (i.e. updated)
    :return: URL safe app name derived from `app_title`
    """
    with ZipFile(file, "r") as z:
        apppath = None
        for zpath in z.namelist():
            if os.path.basename(zpath) == "renv.lock":
                apppath = os.path.dirname(zpath)
                break

        if apppath is None:
            raise FileNotFoundError("required renv.lock file not found")

        ignore_patterns = [
            re.compile(pttrn, re.I) for pttrn in (r"^\..+", r"^renv/", r".+\.rproj$", r"^makefile$", r"^readme.md$")
        ]
        members = []
        for zpath in z.namelist():
            if zpath.startswith(apppath):
                if apppath:
                    relpath = zpath[len(apppath) + 1 :]
                else:
                    relpath = zpath

                if all(pttrn.match(relpath) is None for pttrn in ignore_patterns):
                    members.append(os.path.join(apppath, relpath))

        appname = re.sub("[^a-z0-9_-]", "", app_title.lower().replace(" ", "_"))

        tmptarget = mkdtemp("_new_app")
        z.extractall(tmptarget, members)

        deploytarget = os.path.join(settings.APPS_DEPLOYMENT["upload_path"], appname)

        if os.path.exists(deploytarget):
            if replace:
                shutil.rmtree(deploytarget)
            else:
                raise FileExistsError(f'Deployed app already exists at location "{deploytarget}"')

        shutil.move(os.path.join(tmptarget, apppath), deploytarget)

        if replace:
            (pathlib.Path(deploytarget) / "restart.txt").touch()

        return appname
