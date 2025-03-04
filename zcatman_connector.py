# File: zcatman_connector.py
# Copyright (c) 2020-2021 Splunk Inc.
#
# SPLUNK CONFIDENTIAL - Use or disclosure of this material in whole or in part
# without a valid written license from Splunk Inc. is PROHIBITED.
# -*- coding: utf-8 -*-
# -----------------------------------------
# Phantom sample App Connector python file
# -----------------------------------------

# Python 3 Compatibility imports
from __future__ import print_function, unicode_literals
import glob
import json
import os
import requests
import secrets
import string
import subprocess
import tarfile
import traceback
import uuid
from base64 import b64encode

# Phantom App imports
import phantom.app as phantom
from phantom.action_result import ActionResult
from phantom.base_connector import BaseConnector
from phantom.vault import Vault

from urllib3.exceptions import InsecureRequestWarning

# Usage of the consts file is recommended
# from zcatman_consts import *

# Suppress only the single warning from urllib3 needed.
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


class RetVal(tuple):
    def __new__(cls, val1, val2=None):
        return tuple.__new__(RetVal, (val1, val2))


class ZcatmanConnector(BaseConnector):
    def __init__(self):

        # Call the BaseConnectors init first
        super(ZcatmanConnector, self).__init__()

    def _rest_call(
        self,
        base_url,
        endpoint,
        method="get",
        headers=None,
        params=None,
        data=None,
        json=None,
        github_download=False,
        use_soar_auth=True,
    ):
        try:
            request_method = getattr(requests, method)
        except AttributeError:
            return False, "invalid method: {}".format(method)

        url = f"{base_url}{endpoint}"

        response_data = None

        self.debug_print("URLY", url)

        auth = None
        if use_soar_auth:
            auth = (self.soar_username, self.soar_password)
            headers = None

        try:
            r = None
            r = request_method(
                url,
                headers=headers,
                verify=False,
                params=params,
                data=data,
                auth=auth,
                json=json,
            )
            if not (github_download):
                response_data = r.json()
            else:
                response_data = r.content

            if 200 <= r.status_code < 399:
                return RetVal(True, response_data)

            return False, response_data

        except Exception:
            return False, "Request error: {}".format(traceback.format_exc())

    def _handle_test_connectivity(self, param):
        # Add an action result object to self (BaseConnector)
        # to represent the action for this param
        action_result = self.add_action_result(ActionResult(dict(param)))
        self.save_progress("Testing Connectivity to Splunk SOAR Instance")
        try:
            self.save_progress("Logging in with username + password")
            status, response = self._rest_call(
                self.get_phantom_base_url_formatted(),
                "/rest/container/",
                method="get",
                use_soar_auth=True,
            )
            if not status:
                self.save_progress("Failed to login - {}".format(response))
                return action_result.set_status(
                    phantom.APP_ERROR,
                    "Test Connectivity Failed - "
                    "Unable to connect to Splunk SOAR - "
                    "Check username and password",
                )
            self.save_progress("Succesfully connected with username + password")
            self.save_progress("Checking for GitHub connectivity")
            folder_list = self._list_github_folders()
            if not folder_list:
                return action_result.set_status(
                    phantom.APP_ERROR,
                    "No folders found in github repo and branch. "
                    "Check if github access token ,github repo path, "
                    "or github branch  correctly.",
                )
            self.save_progress("Succesfully connected to GitHub")
            return action_result.set_status(
                phantom.APP_SUCCESS, "Test Connectivity Successful"
            )
        except Exception:
            return action_result.set_status(phantom.APP_ERROR, traceback.format_exc())

    def get_instance_name(self):
        try:
            _, settings_response = self._rest_call(
                self.get_phantom_base_url_formatted(),
                f"/rest/system_settings",
                method="get",
                use_soar_auth=True,
            )
            if settings_response.get('company_info_settings'):
                return True, settings_response['company_info_settings']['system_name']
        except Exception as e:
            return (
                False,
                "Error occurred getting instance name. Details - {}".format(e.message)
            )

    def get_phantom_base_url_formatted(self):
        try:
            config = self.get_config()
            phantom_base_url = config["soar_base_url"]

            if phantom_base_url.endswith("/"):
                phantom_base_url = phantom_base_url[:-1]

            return phantom_base_url
        except Exception as err:
            return (
                False,
                "Error occurred getting phantom base url. Details - {}".format(
                    err.message
                ),
            )

    def get_automation_key(self, username=None, user_id=None):
        if username:
            user_id = self.get_user_id(username)
        if user_id:
            _, token_response = self._rest_call(
                self.get_phantom_base_url_formatted(),
                f"/rest/ph_user/{user_id}/token",
                method="get",
                use_soar_auth=True,
            )
            if token_response.get("key"):
                return True, token_response["key"]
            else:
                return False, "No automation toke found"
        return False, "Must provide username or user_id"

    def get_user_id(self, username):
        params = {"include_automation": True, "_filter_username": f'"{username}"'}
        status, user_response = self._rest_call(
            self.get_phantom_base_url_formatted(),
            "/rest/ph_user",
            params=params,
            use_soar_auth=True,
        )
        if not status:
            return False
        if user_response["count"] == 0:
            return False
        return user_response["data"][0]["id"]

    def get_role_id(self, role_name):
        params = {"_filter_name": f'"{role_name}"'}
        status, role_response = self._rest_call(
            self.get_phantom_base_url_formatted(),
            "/rest/role",
            params=params,
            use_soar_auth=True,
        )
        if not status:
            return False
        if role_response["count"] == 0:
            return False
        return role_response["data"][0]["id"]

    def add_role_to_user(self, username, role_name):
        user_id = self.get_user_id(username)
        role_id = self.get_role_id(role_name)
        if user_id and role_id:
            data = {
                "add_roles": [role_id]
            }
            status, update_message = self._rest_call(
                self.get_phantom_base_url_formatted(),
                f"/rest/ph_user/{user_id}",
                method='post',
                json=data,
                use_soar_auth=True,
            )
            if status:
                return True
        return False

    def create_user(
        self,
        username,
        password=None,
        generate_pass=True,
        automation=True,
        allowed_ips=None,
        roles=None,
    ):
        data = {"username": username, "roles": roles}

        if automation:
            data["type"] = "automation"
            data["allowed_ips"] = [allowed_ips]
        else:
            if generate_pass:
                alphabet = string.ascii_letters + string.digits + string.punctuation
                password = "".join(secrets.choice(alphabet) for i in range(20))

            data["password"] = password

        status, response = self._rest_call(
            self.get_phantom_base_url_formatted(),
            "/rest/ph_user",
            method="post",
            json=data,
            use_soar_auth=True,
        )
        if not status and "already exists" not in response.get("message", ""):
            return False, f"Error creating user: {username}"

        user_id = response.get("id", self.get_user_id(username))
        if automation:
            _, password = self.get_automation_key(user_id=user_id)

        return True, {"id": user_id, "password": password}

    def _handle_container_labels(self, container_label):
        status, response = self._rest_call(
            self.get_phantom_base_url_formatted(),
            "/rest/container_options",
            method="get",
        )
        existing_labels = response["label"]
        if container_label not in existing_labels:
            data = {"add_label": True, "label_name": container_label}
            label_add_status, label_add_response = self._rest_call(
                self.get_phantom_base_url_formatted(),
                "/rest/system_settings/events",
                json=data,
                method="post",
            )
            if not (label_add_status):
                return False, "Error adding label - {}".format(label_add_response)
            return True, "Labels succesfully added"
        elif container_label in existing_labels:
            return True, "Labels exist"
        else:
            return False, "Error checking labels - {}".format(response)

    def _handle_update_object(self, param):
        self.save_progress(
            "In action handler for: {0}".format(self.get_action_identifier())
        )
        action_result = self.add_action_result(ActionResult(dict(param)))

        status, response = self._get_github_data()
        if not (status):
            return action_result.set_status(phantom.APP_ERROR, response)

        status, untar_response = self._save_github_data(response)
        if not (status):
            return action_result.set_status(phantom.APP_ERROR, response)

        github_path = param["github_path"]

        if "assets/" in github_path.lower():
            status, response = self.update_an_asset(untar_response, github_path)
        elif "playbooks/" in github_path.lower():
            status, response = self.update_a_playbook(untar_response, github_path)
        elif "demo_config__containers/" in github_path.lower():
            status, response = self.update_containers(
                untar_response, github_path=github_path, single=True
            )
            if not (status):
                return action_result.set_status(
                    phantom.APP_ERROR,
                    f"Whoops. That didn't work. "
                    f"If you're trying to update a demo_configuration container, you'll need to delete it first. "
                    f"Hopefully that won't be a requirement forever, but it is for now. Deal with it. "
                    f"Details - {str(response) if response else None}",
                )
        elif "compiled_apps/" in github_path.lower():
            status, response = self.update_an_app(untar_response, github_path)

        if not (status):
            return action_result.set_status(
                phantom.APP_ERROR,
                "Unable to load object. Details - {}".format(
                    (str(response) if response else "None")
                ),
            )

        return action_result.set_status(
            phantom.APP_SUCCESS, "Successfully loaded object."
        )

    def update_an_asset(self, file_directory, github_path):
        self.save_progress("Loading asset")
        asset_file = glob.glob("{}/*{}".format(file_directory, github_path))
        if len(asset_file) < 1:
            return False, "Unable to get asset from github data"

        asset_file_data = None
        with open(asset_file[0], "r") as asset_file_stream:
            asset_file_data = asset_file_stream.read()

        status, response = self.seek_and_destroy("asset", json.loads(asset_file_data))
        if not (status):
            return (
                status,
                "Unable to determine existence of asset. Details - {}".format(
                    str(response) if response else "None"
                ),
            )

        asset_status, asset_response = self._rest_call(
            self.get_phantom_base_url_formatted(),
            "/rest/asset",
            data=asset_file_data,
            method="post",
        )
        if not (asset_status):
            return (
                asset_status,
                "Unable to load assets. File - {}. Details - {}".format(
                    asset_file_data, (str(asset_response) if asset_response else "None")
                ),
            )

        return True, "Asset successfully loaded"

    def update_a_playbook(self, file_directory, github_path):
        self.save_progress("Loading playbook")
        playbook_file = glob.glob("{}/*{}".format(file_directory, github_path))
        if len(playbook_file) < 1:
            return False, "Unable to get playbok from github data"

        playbook_file_data = None
        with open(playbook_file[0], "rb") as playbook_file_stream:
            playbook_file_data = playbook_file_stream.read()

        playbook_file_data = b64encode(playbook_file_data)
        payload = {
            "playbook": playbook_file_data.decode("utf-8"),
            "scm": "local",
            "force": True,
        }
        status, response = self._rest_call(
            self.get_phantom_base_url_formatted(),
            "/rest/import_playbook",
            json=payload,
            method="post",
        )
        if not (status):
            return status, "Unable to load playbook. Details - {}".format(
                (str(response) if response else "None")
            )

        return True, "Successfully loaded playbook"

    def update_an_app(self, file_directory, github_path):
        self.save_progress("Loading app")
        app_file = glob.glob("{}/*{}".format(file_directory, github_path))
        if len(app_file) < 1:
            return False, "Unable to get app from github data"

        app_file_data = None
        with open(app_file[0], "rb") as app_file_stream:
            app_file_data = app_file_stream.read()

        app_file_data = b64encode(app_file_data)
        payload = {"app": app_file_data.decode("utf-8")}
        try:
            status, app_response = self._rest_call(
                self.get_phantom_base_url_formatted(),
                "/rest/app",
                method="post",
                json=payload,
            )
            if phantom.is_fail(status):
                return False, "Unable to install app. File - {}. Details - {}".format(
                    app_file[0], (str(app_response) if app_response else "None")
                )
        except Exception:
            return (
                False,
                "Error occurred during app install. File - {}. Error - {}".format(
                    app_file[0], traceback.format_exc()
                ),
            )

        return True, "Successfully updated app."

    def _list_github_folders(self):
        self.save_progress("Retrieving github folder list")
        folder_list = []

        github_header = {}

        if self.github_personal_access_token:
            github_header = {
                "Authorization": "token {}".format(self.github_personal_access_token)
            }

        github_base_url = "https://api.github.com"

        github_repo_path = self.github_repo_path
        if not (github_repo_path.startswith("/")):
            github_repo_path = "/{}".format(github_repo_path)
        if github_repo_path.endswith("/"):
            github_repo_path = github_repo_path[:-1]

        github_branch = self.github_branch.replace("/", "")

        github_endpoint = "/repos{}/contents/?ref={}".format(
            github_repo_path, github_branch
        )

        status, response = self._rest_call(
            github_base_url, github_endpoint, headers=github_header, use_soar_auth=False
        )

        if status:
            for item in response:
                folder_list.append(item["path"])

        return folder_list

    def _get_github_data(self):
        self.save_progress("Retrieving github demo data")
        github_header = {}

        if self.github_personal_access_token:
            github_header = {
                "Authorization": "token {}".format(self.github_personal_access_token)
            }

        github_base_url = "https://api.github.com"

        github_repo_path = self.github_repo_path
        if not (github_repo_path.startswith("/")):
            github_repo_path = "/{}".format(github_repo_path)
        if github_repo_path.endswith("/"):
            github_repo_path = github_repo_path[:-1]

        github_tarball_path = "/tarball/" + self.github_branch.replace("/", "")

        github_endpoint = "/repos{}{}".format(github_repo_path, github_tarball_path)

        status, response = self._rest_call(
            github_base_url,
            github_endpoint,
            headers=github_header,
            github_download=True,
            use_soar_auth=False,
        )

        return status, response

    def _save_github_data(self, content):
        self.save_progress("Unpacking github demo data")
        tmp_vault_directory = Vault.get_vault_tmp_dir()
        unique_id = uuid.uuid4()

        file_path = "{}/{}".format(tmp_vault_directory, unique_id)
        print(file_path)
        try:
            with open("{}.tar".format(file_path), "wb") as tarball:
                tarball.write(content)
            tf = tarfile.open("{}.tar".format(file_path), mode="r")
            tf.extractall(path="{}".format(file_path))
            tf.close()
        except Exception as err:
            return False, "Error occurred unpacking tarball. Details - {}".format(
                err.message
            )

        return True, file_path

    def update_containers(
        self,
        file_directory,
        github_path="demo_config__containers",
        single=False,
        do_not_destroy=False,
        param=None,
    ):

        demo_container_dir = glob.glob("{}/*/{}".format(file_directory, github_path))
        if len(demo_container_dir) < 1:
            return False, "Unable to get demo_config__conatiners from github data"

        if param:
            duplicate_containers = param.get(
                "duplicate_containers_across_tenants", False
            )

        # Get tenancy status
        try:
            status, response = self._rest_call(
                self.get_phantom_base_url_formatted(),
                '/rest/system_settings?sections=["multi_tenant"]',
            )
            multi_tenancy_enabled = response.get("multi_tenant", {}).get(
                "enabled", False
            )
            if multi_tenancy_enabled:
                # Get tenant list
                tenant_id_list = []
                status, response = self._rest_call(
                    self.get_phantom_base_url_formatted(), "/rest/tenant?page_size=0"
                )
                for tenant in response.get("data"):
                    tenant_id_list.append(tenant["id"])
        except Exception as err:
            return (
                False,
                "Unable to get tenancy status - Error: {} - Response: {}".format(
                    str(err), str(response)
                ),
            )

        if "demo_config" in github_path:
            parameters = {
                "_filter_label__iexact": '"demo_configuration"',
                "page_size": 0,
            }
            if not (single) and not (do_not_destroy):
                status, response = self._rest_call(
                    self.get_phantom_base_url_formatted(),
                    "/rest/container",
                    params=parameters,
                )
                if not (status):
                    return (
                        False,
                        "Unable to retrieve demo_configuration containers. Details - {}".format(
                            str(response) if response else "None"
                        ),
                    )

                for container in response["data"]:
                    status, response = self._rest_call(
                        self.get_phantom_base_url_formatted(),
                        "/rest/container/{}".format(container["id"]),
                        method="delete",
                    )
                    if not (status):
                        return (
                            False,
                            "Unable to delete demo_configuration containers. Details - {}".format(
                                str(response) if response else "None"
                            ),
                        )

        try:
            last_file = []
            for root, dirs, files in os.walk(demo_container_dir[0]):
                for file_ in files:
                    container_folder_path = root.split(github_path)[1]
                    if ".json" in file_ and "vault" not in container_folder_path:
                        container_data = None
                        with open(os.path.join(root, file_), "r") as container_file:
                            container_data = container_file.read()

                        json_container_data = json.loads(container_data)

                        if "demo_config" not in github_path and not (do_not_destroy):
                            self.seek_and_destroy(
                                "seed_containers", json_container_data
                            )

                        json_container_artifacts = json_container_data.get(
                            "artifacts", []
                        )
                        json_container_data = json_container_data["container"]
                        if "id" in json_container_data:
                            json_container_data.pop("id")
                        if "ingest_app_id" in json_container_data:
                            json_container_data.pop("ingest_app_id")
                        if "asset_id" in json_container_data:
                            json_container_data.pop("asset_id")
                        if "current_phase_id" in json_container_data:
                            json_container_data.pop("current_phase_id")
                        for artifact in json_container_artifacts:
                            if "id" in artifact:
                                artifact.pop("id")
                            if "ingest_app_id" in artifact:
                                artifact.pop("ingest_app_id")
                        json_container_data["artifacts"] = json_container_artifacts

                        # Check labels before adding container
                        (
                            label_check_status,
                            label_check_message,
                        ) = self._handle_container_labels(json_container_data["label"])
                        if not (label_check_status):
                            return False, "{}".format(label_check_message)

                        # add containers across all tenants if this is the "seed_container" action and the user selected to duplicate containers
                        if (
                            multi_tenancy_enabled
                            and not duplicate_containers
                            and github_path != "demo_config__containers"
                        ):
                            last_file = []
                            json_container_data["tenant_id"] = 0
                            status, message, container_id = self.save_container(
                                json_container_data
                            )
                            if phantom.is_fail(status):
                                return (
                                    False,
                                    "Error adding container to default tenant. File - {}. Details - {}/{}".format(
                                        root,
                                        file_,
                                        (str(message) if message else "None"),
                                    ),
                                )
                            last_file.append(container_id)
                        elif multi_tenancy_enabled and duplicate_containers:
                            last_file = []
                            for tenant_id in tenant_id_list:
                                json_container_data["tenant_id"] = tenant_id
                                status, message, container_id = self.save_container(
                                    json_container_data
                                )
                                if phantom.is_fail(status):
                                    return (
                                        False,
                                        "Error adding container to tenant. {}. File - {}. Details - {}/{}".format(
                                            tenant_id,
                                            root,
                                            file_,
                                            (str(message) if message else "None"),
                                        ),
                                    )
                                last_file.append(container_id)
                        else:
                            last_file = []
                            status, message, container_id = self.save_container(
                                json_container_data
                            )
                            if phantom.is_fail(status):
                                return (
                                    False,
                                    "Error adding container. File - {}. Details - {}/{}".format(
                                        root,
                                        file_,
                                        (str(message) if message else "None"),
                                    ),
                                )
                            last_file.append(container_id)
                    elif "vault" in container_folder_path:
                        vault_contents = None
                        with open(os.path.join(root, file_), "rb") as vault_file:
                            vault_contents = vault_file.read()
                        serialized_contents = b64encode(vault_contents)
                        for container_id in last_file:
                            attachment_json = {
                                "container_id": container_id,
                                "file_content": serialized_contents.decode("utf-8"),
                                "file_name": file_,
                            }
                            status, vault_response = self._rest_call(
                                self.get_phantom_base_url_formatted(),
                                "/rest/container_attachment",
                                method="post",
                                json=attachment_json,
                            )
                            if not (status):
                                return (
                                    False,
                                    "Unable to upload vault data. File - {}. Details - {}".format(
                                        file_,
                                        (
                                            str(vault_response)
                                            if vault_response
                                            else "None"
                                        ),
                                    ),
                                )

        except Exception:
            return False, "Error during container load - {}".format(
                traceback.format_exc()
            )

        return True, "Successfully loaded {} container data".format(github_path)

    def update_apps(self, file_directory):
        apps_dir = glob.glob("{}/*/compiled_apps".format(file_directory))
        if len(apps_dir) < 1:
            return False, "Unable to get compiled_apps from github data"

        for root, dirs, files in os.walk(apps_dir[0]):
            for file_ in files:
                app_file_data = None
                with open(os.path.join(root, file_), "rb") as app_file:
                    app_file_data = app_file.read()
                app_file_data = b64encode(app_file_data)
                payload = {"app": app_file_data.decode("utf-8")}

                status, app_response = self._rest_call(
                    self.get_phantom_base_url_formatted(),
                    "/rest/app",
                    method="post",
                    json=payload,
                )
                if not (status):
                    return (
                        False,
                        "Unable to install app. File - {}. Details - {}".format(
                            file_, (str(app_response) if app_response else "None")
                        ),
                    )

        return True, "Successfully loaded apps"

    def update_roles(self, file_directory):
        roles_dir = glob.glob("{}/*/roles".format(file_directory))
        if len(roles_dir) < 1:
            return True, "Roles not found in github"
        for root, dirs, files in os.walk(roles_dir[0]):
            for file_ in files:
                role_file_data = None
                with open(os.path.join(root, file_), "rb") as role_file:
                    role_file_data = role_file.read()
                self.seek_and_destroy("roles", json.loads(role_file_data))

                status, role_response = self._rest_call(
                    self.get_phantom_base_url_formatted(),
                    "/rest/role",
                    method="post",
                    json=json.loads(role_file_data),
                )
                if not (status):
                    return False, "Unable to load role. File - {}. Details - {}".format(
                        file_, (str(role_response) if role_response else "None")
                    )

        return True, "Successfully loaded roles"

    def update_users(self, file_directory):
        users_dir = glob.glob("{}/*/users".format(file_directory))
        if len(users_dir) < 1:
            return True, "Users not found in github data"

        for root, dirs, files in os.walk(users_dir[0]):
            for file_ in files:
                user_file_data = None
                with open(os.path.join(root, file_), "rb") as user_file:
                    user_file_data = user_file.read()
                self.seek_and_destroy("users", json.loads(user_file_data))
                try:
                    status, user_response = self._rest_call(
                        self.get_phantom_base_url_formatted(),
                        "/rest/ph_user",
                        method="post",
                        data=user_file_data,
                    )
                except Exception:
                    return False, "Unable to load user. File - {}. Details - {}".format(
                        file_, (str(user_response) if user_response else "None")
                    )

        return True, "Successfully loaded users"

    # def live_replace(self, object_data_as_string):
    #     regex = r"\|\|([A-z\_0-9\:]+)\|\|"
    #     matches = re.finditer(regex, object_data_as_string, re.MULTILINE)
    #     for match in matches:
    #         if len(match.groups()) == 1:
    #             placeholder = match.group(0)
    #             placeholder_parts = placeholder.split('__')
    #             endpoint = placeholder[0]
    #             field = placeholder[1].split(':')
    #             value = field[1]
    #             field = field[0]
    #             filter_params = {
    #                 '_filter_{}__iexact'.format().field: '"{}"'.format(value)
    #             }
    #             status, response = self._rest_call(self.get_phantom_base_url_formatted(), '/rest/{}'.format(endpoint), method='get', params=filter_params)
    #             if not(status):
    #                 return status, 'Unable to live replace values - {} {} {}'.format(endpoint, field, value)
    #             replacement_value = response['data'][0][placeholder[1]]
    #             object_data_as_string.replace(match.group(0), replacement_value)

    def seek_and_destroy(self, object_type, object_data, do_not_destroy=False):
        filter_params = None
        endpoint = None

        if object_type == "asset":
            filter_params = {"_filter_name__iexact": '"{}"'.format(object_data["name"])}
            endpoint = "/rest/asset"

        if object_type == "response_template":
            filter_params = {"_filter_name__iexact": '"{}"'.format(object_data["name"])}
            endpoint = "/rest/workbook_template"
        elif object_type == "demo_config__containers":
            filter_params = {"_filter_id": "{}".format(object_data["container_id"])}
            endpoint = "/rest/container"

        if object_type == "seed_containers":
            filter_params = {
                "_filter_name__iexact": '"{}"'.format(object_data["container"]["name"])
            }
            endpoint = "/rest/container"

        if object_type == "roles":
            filter_params = {"_filter_name__iexact": '"{}"'.format(object_data["name"])}
            endpoint = "/rest/role"

        if object_type == "users":
            filter_params = {
                "_filter_username__iexact": '"{}"'.format(object_data["username"])
            }
            endpoint = "/rest/ph_user"

        status, response = self._rest_call(
            self.get_phantom_base_url_formatted(), endpoint, params=filter_params
        )
        if not (status):
            return (
                status,
                "Unable to search for existance of object. Details - {}".format(
                    (str(response) if response else "None")
                ),
            )

        if do_not_destroy:
            return status, response["data"]

        if len(response["data"]) == 1:
            status, response = self._rest_call(
                self.get_phantom_base_url_formatted(),
                "{}/{}".format(endpoint, response["data"][0]["id"]),
                method="delete",
            )
            if not (status):
                return status, "Unable to delete existing object. Details - {}".format(
                    (str(response) if response else "None")
                )

        return True, "Successfully sought and destroyed"

    def update_assets(self, file_directory):
        # config = self.get_config()
        assets_dir = glob.glob("{}/*/assets".format(file_directory))
        if len(assets_dir) < 1:
            return False, "Unable to get assets from github data"

        for root, dirs, files in os.walk(assets_dir[0]):
            for file_ in files:
                print(file_)
                with open(os.path.join(root, file_), "r") as asset_file:
                    asset_file_data = asset_file.read()
                # Comment out seek and destroy since overwriting existing assets is no longer desirable
                ''' 
                status, response = self.seek_and_destroy(
                    "asset", json.loads(asset_file_data)
                )
                
                if not (status):
                    return (
                        status,
                        "Unable to check existance asset data. File - {}. Details - {}".format(
                            file_, response
                        ),
                    )
                '''
                asset_status, asset_response = self._rest_call(
                    self.get_phantom_base_url_formatted(),
                    "/rest/asset",
                    data=asset_file_data,
                    method="post",
                )
                if not (asset_status):
                    if asset_response.get('message', '') == 'Error: Asset name already in use.':
                        continue
                    else:
                        return (
                            asset_status,
                            "Unable to load assets. File - {}. Details - {}".format(
                                file_, (str(asset_response) if asset_response else "None")
                            ),
                        )

        return True, "Successfully loaded assets"

    def update_custom_functions(self, file_directory):
        custom_function_dir = glob.glob("{}/*/custom_functions".format(file_directory))
        if len(custom_function_dir) < 1:
            return False, "Unable to get custom_functions from github data"

        for root, dirs, files in os.walk(custom_function_dir[0]):
            for file_ in files:
                with open(os.path.join(root, file_), "rb") as custom_function_file:
                    custom_function_file_data = custom_function_file.read()
                custom_function_file_data = b64encode(custom_function_file_data)
                payload = {
                    "custom_function": custom_function_file_data.decode("utf-8"),
                    "scm": "local",
                    "force": True,
                }
                status, response = self._rest_call(
                    self.get_phantom_base_url_formatted(),
                    "/rest/import_custom_function",
                    json=payload,
                    method="post",
                )
                if not (status):
                    return (
                        status,
                        "Unable to load custom_function. File - {}. Details - {}".format(
                            file_, (str(response) if response else "None")
                        ),
                    )

        return True, "Successfully loaded custom_functions"

    def update_playbooks(self, file_directory):
        playbooks_dir = glob.glob("{}/*/playbooks".format(file_directory))
        settings_json = glob.glob(
            "{}/*/custom_settings/playbook_settings.json".format(file_directory)
        )
        # pull in list of playbooks that should be active
        if settings_json:
            with open(settings_json[0], "r") as active_file:
                extracted_json = json.loads(active_file.read())
                active_playbooks = extracted_json.get("active_playbooks")
                no_active_list = extracted_json.get('no_active')
            # check if this instance should have no active playbooks for ES 8
            if active_playbooks and no_active_list:
                _, instance_name = self.get_instance_name()
                if instance_name in no_active_list:
                    active_playbooks = None
        else:
            active_playbooks = None
        
        for root, dirs, files in os.walk(playbooks_dir[0]):
            for file_ in files:
                if file_.endswith(".tgz"):
                    with open(os.path.join(root, file_), "rb") as playbook_file:
                        playbook_file_data = playbook_file.read()
                    playbook_file_data = b64encode(playbook_file_data)
                    payload = {
                        "playbook": playbook_file_data.decode("utf-8"),
                        "scm": "local",
                        "force": True,
                    }
                    status, response = self._rest_call(
                        self.get_phantom_base_url_formatted(),
                        "/rest/import_playbook",
                        json=payload,
                        method="post",
                    )
                    if not (status):
                        return (
                            status,
                            "Unable to load playbooks. File - {}. Details - {}. Payload - {}".format(
                                file_,
                                (str(response) if response else "None"),
                                str(payload),
                            ),
                        )
                    playbook_name = file_.replace(".tgz", "")
                    # Attempt to activate playbooks that user marked active
                    if active_playbooks and playbook_name in active_playbooks:
                        # Translate playbook_name to id as /rest/import_playbook does not return playbook_id
                        params = {
                            "_filter_scm": 2,
                            "_filter_name": '"{}"'.format(playbook_name),
                        }
                        status, response = self._rest_call(
                            self.get_phantom_base_url_formatted(),
                            "/rest/playbook",
                            params=params,
                            method="get",
                        )
                        try:
                            for item in response["data"]:
                                payload = {"active": True}
                                status, response = self._rest_call(
                                    self.get_phantom_base_url_formatted(),
                                    "/rest/playbook/{}".format(item["id"]),
                                    json=payload,
                                    method="post",
                                )

                        except Exception as e:
                            return (
                                False,
                                'Playbooks loaded successfully but unable to activate: "{}" - error message: {}'.format(
                                    playbook_name, e
                                ),
                            )

        return True, "Successfully loaded playbooks"

    def update_response_templates(self, file_directory):
        response_templates_dir = glob.glob(
            "{}/*/response_templates".format(file_directory)
        )
        if len(response_templates_dir) < 1:
            return True, "No response templates to get"

        for root, dirs, files in os.walk(response_templates_dir[0]):
            for file_ in files:
                with open(os.path.join(root, file_), "r") as response_template_file:
                    response_template_data = response_template_file.read()
                status, response = self.seek_and_destroy(
                    "response_template",
                    json.loads(response_template_data),
                    do_not_destroy=True,
                )
                if not (status):
                    return (
                        status,
                        "Unable to check existence of response template. File - {}. Details - {}".format(
                            file_, (str(response) if response else "None")
                        ),
                    )
                workflow_template_id = ""
                if len(response) > 0:
                    workflow_template_id = "/{}".format(response[0]["id"])
                status, response = self._rest_call(
                    self.get_phantom_base_url_formatted(),
                    "/rest/workbook_template{}".format(workflow_template_id),
                    data=response_template_data,
                    method="post",
                )
                if not (status):
                    return (
                        status,
                        "Unable to load response templates. File - {}. Details - {}".format(
                            file_, (str(response) if response else "None")
                        ),
                    )

        return True, "Successfully loaded response templates"

    def set_up_session(self):
        s = requests.Session()
        s.verify = False
        url = self.get_phantom_base_url_formatted()
        s.headers['Referer'] = url + '/soar_login'
        response = s.get(url + '/soar_login', verify=False)
        if response.status_code == 404:
            s.headers['Referer'] = url + '/login'
            s.get(url + 'login', verify=False)
        csrf = response.cookies['csrftoken']
        login_resp = s.post(
            url + '/login',
            data={
                'username': self.soar_username,
                'password': self.soar_password,
                'csrfmiddlewaretoken': csrf
            }, verify=False
        )
        login_resp.raise_for_status()
        s.session_id = s.cookies["sessionid"]
        csrf = s.cookies['csrftoken']
        s.headers['X-CSRFToken'] = csrf
        return s

    def update_system_settings_helper(self, system_settings_file):

        with open(system_settings_file[0], "r") as active_file:
            ss_json = json.loads(active_file.read())

            if ss_json:
                if ss_json.get('add_admin_to_automation_user'):
                    self.add_role_to_user(username="automation", role_name="Administrator")
                if ss_json.get("mock_app"):
                    if ss_json["mock_app"].get("create_automation_user"):
                        roles = ["Automation"]
                        if ss_json["mock_app"].get("give_admin"):
                            roles.append("Administrator")
                        status, automation_account = self.create_user(
                            username="soar_automation",
                            automation=True,
                            roles=roles,
                            allowed_ips="any",
                        )
                        if not status:
                            return (
                                False,
                                f"Unable to create_automation_user - {automation_account}",
                            )
                        automation_token = automation_account["password"]
                    elif ss_json["mock_app"].get("automation_account"):
                        status, automation_token = self.get_automation_key(
                            username=ss_json["mock_app"]["automation_account"]
                        )
                        if not status:
                            return False, automation_token
                    result, instance_name = self.get_instance_name()
                    if not result:
                        instance_name = "Unknown"
                    envvars_data = {
                            "env-MOCK_API_URL-name": "MOCK_API_URL",
                            "env-MOCK_API_URL-type": "text",
                            "env-MOCK_API_URL-value": ss_json['mock_app'].get('mock_api_url', ''),
                            "env-MOCK_API_LOCATION-name": "MOCK_API_LOCATION",
                            "env-MOCK_API_LOCATION-type": "text",
                            "env-MOCK_API_LOCATION-value": ss_json['mock_app'].get('mock_api_location', ''),
                            "env-MOCK_API_TOKEN-name": "MOCK_API_TOKEN",
                            "env-MOCK_API_TOKEN-type": "password",
                            "env-MOCK_API_TOKEN-value": ss_json['mock_app'].get('mock_api_token', ''),
                            "env-SYSTEM_NAME-name": "SYSTEM_NAME",
                            "env-SYSTEM_NAME-type": "text",
                            "env-SYSTEM_NAME-value": instance_name,
                            "env-NO_PROXY-name": "NO_PROXY",
                            "env-NO_PROXY-type": "text",
                            "env-NO_PROXY-value": "127.0.0.1,localhost"
                    }
                    soar_session = self.set_up_session()
                    resp = soar_session.post(
                        self.get_phantom_base_url_formatted() + '/admin/admin_settings/envvars/',
                        data=envvars_data, verify=False
                    )
                    resp.raise_for_status()
 
                if ss_json.get("eula_accepted"):
                    self._rest_call(self.get_phantom_base_url_formatted(), '/rest/accept_eula', json={}, method='post')
                system_settings = {}
                if ss_json.get("administrator_contact"):
                    system_settings['administrator_contact'] = ss_json["administrator_contact"]
                if ss_json.get("company_name"):
                    system_settings['company_name'] = ss_json["company_name"]
                if ss_json.get("system_name"):
                    system_settings['system_name'] = ss_json["system_name"]
                if ss_json.get("fqdn"):
                    if ss_json["fqdn"] == "$$PHANTOM_BASE_URL$$":
                        system_settings['fqdn'] = self.get_phantom_base_url_formatted()
                    else:
                        system_settings['fqdn'] = ss_json["fqdn"]
                if system_settings:
                    self._rest_call(self.get_phantom_base_url_formatted(), '/rest/system_settings/info', json=system_settings, method='post')
                if ss_json.get("disable_admin_onboarding"):
                    admin_settings = {
                        "show_onboarding": False,
                        "onboarding_state": {"redirect_onboarding": False}
                    }
                    self._rest_call(self.get_phantom_base_url_formatted(), '/rest/ph_user/1', json=admin_settings, method='post')
                if ss_json.get('load_license'):
                    license_data = {
                        'license': ss_json['load_license']
                    }
                    self._rest_call(self.get_phantom_base_url_formatted(), '/rest/license', json=license_data, method='post')
        return True, "- Loaded system settings"

    def update_severity_settings_helper(self, severity_settings_file):
        with open(severity_settings_file[0], "r") as active_file:
            settings_json = json.loads(active_file.read())
            custom_severities = settings_json.get("custom_severities")
            severity_order = settings_json.get("severity_order")

        if custom_severities:
            # Get existing severities:
            status, existing_severities = self._rest_call(
                self.get_phantom_base_url_formatted(), "/rest/severity"
            )
            if not status:
                return status, "Unable to get existing severities - {}".format(
                    existing_severities
                )
            existing_severities = [item["name"] for item in existing_severities["data"]]

            # Update severities
            for severity in custom_severities:
                if severity["name"] not in existing_severities:
                    status, response = self._rest_call(
                        self.get_phantom_base_url_formatted(),
                        "/rest/severity",
                        json=severity,
                        method="post",
                    )
                    if not status:
                        return status, "Unable to load severity - {0} - {1}".format(
                            severity, response
                        )

        if severity_order:
            data = {"names": severity_order}
            status, response = self._rest_call(
                self.get_phantom_base_url_formatted(),
                "/rest/rank_severities",
                json=data,
                method="post",
            )
            if not (status):
                return status, "Unable to rank severity - {0} - {1}".format(
                    severity_order, response
                )

        return True, "- Loaded custom severities"

    def custom_settings_handler(self, file_directory):
        system_message = None
        severity_message = None
        severity_settings = glob.glob(
            "{}/*/custom_settings/severity_settings.json".format(file_directory)
        )
        system_settings = glob.glob(
            "{}/*/custom_settings/system_settings.json".format(file_directory)
        )
        if severity_settings:
            status, severity_message = self.update_severity_settings_helper(
                severity_settings
            )
            if not status:
                return status, severity_message
        if system_settings:
            status, system_message = self.update_system_settings_helper(system_settings)
            if not status:
                return status, system_message
        message = "Successfully loaded custom settings. "
        if severity_message:
            message += severity_message
        if system_message:
            message += system_message

        return True, message

    def _handle_load_demo_data(self, param):
        try:
            self.save_progress(
                "In action handler for: {0}".format(self.get_action_identifier())
            )
            action_result = self.add_action_result(ActionResult(dict(param)))

            folder_list = self._list_github_folders()
            if not folder_list:
                return action_result.set_status(
                    phantom.APP_ERROR,
                    "No folders found in github repo and branch. Check github access token (if private repo), github repo path, and github branch were entered correctly.",
                )

            supported_object_list = [
                "roles",
                "users",
                "assets",
                "compiled_apps",
                "demo_config__containers",
                "playbooks",
                "response_templates",
                "seed_containers",
                "custom_functions",
                "custom_settings",
            ]
            unsupported_types = list(set(folder_list).difference(supported_object_list))

            if unsupported_types:
                self.debug_print(
                    "Skipping these unsupported folders: {}".format(unsupported_types)
                )

            object_types = list(set(folder_list).intersection(supported_object_list))
            if not object_types:
                return action_result.set_status(
                    phantom.APP_ERROR, "No compatible items found"
                )
            include_object_types = param.get("include_object_types", "")
            exclude_object_types = param.get("exclude_object_types", "")
            if not include_object_types and not exclude_object_types:
                import_object_list = object_types
                self.save_progress(
                    "No filters provided. Importing these compatible github folders: {}".format(
                        import_object_list
                    )
                )
            else:
                include_object_types = [
                    item.strip().lower()
                    for item in include_object_types.split(",")
                    if item
                ]
                if include_object_types:
                    import_object_list = [
                        item for item in object_types if item in include_object_types
                    ]
                else:
                    import_object_list = object_types
                exclude_object_types = [
                    item.strip().lower()
                    for item in exclude_object_types.split(",")
                    if item
                ]
                import_object_list = [
                    item
                    for item in import_object_list
                    if item not in exclude_object_types
                ]
                self.save_progress(
                    "Importing these compatible github folders based on filter criteria: {}".format(
                        import_object_list
                    )
                )

            status, response = self._get_github_data()
            if not (status):
                return action_result.set_status(phantom.APP_ERROR, response)

            status, untar_response = self._save_github_data(response)
            if not (status):
                return action_result.set_status(phantom.APP_ERROR, response)

            summary = {}

            if "roles" in import_object_list:
                self.save_progress("Loading role data")
                status, message = self.update_roles(untar_response)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["roles_message"] = message

            if "users" in import_object_list:
                self.save_progress("Loading user data")
                status, message = self.update_users(untar_response)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["users_message"] = message

            if "demo_config__containers" in import_object_list:
                self.save_progress("Loading demo configuration container data")
                status, message = self.update_containers(untar_response, param=param)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["demo_config__containers_message"] = message

            if "compiled_apps" in import_object_list:
                self.save_progress("Loading demo app data")
                status, message = self.update_apps(untar_response)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["compiled_apps_message"] = message

            if "assets" in import_object_list:
                self.save_progress("Loading demo asset data")
                status, message = self.update_assets(untar_response)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["assets_message"] = message

            if "playbooks" in import_object_list:
                self.save_progress("Loading demo playbook data")
                status, message = self.update_playbooks(untar_response)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["playbooks_message"] = message

            if "custom_functions" in import_object_list:
                self.save_progress("Loading demo custom function data")
                status, message = self.update_custom_functions(untar_response)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["custom_functions_message"] = message

            if "response_templates" in import_object_list:
                self.save_progress("Loading response templates")
                status, message = self.update_response_templates(untar_response)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["response_templates_message"] = message

            if "custom_settings" in import_object_list:
                self.save_progress("Loading custom settings data")
                status, message = self.custom_settings_handler(untar_response)
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["custom_settings_message"] = message

            if "seed_containers" in import_object_list:
                self.save_progress("Loading seed container data")
                status, message = self.update_containers(
                    untar_response,
                    github_path="seed_containers",
                    do_not_destroy=False,
                    param=param,
                )
                if not (status):
                    return action_result.set_status(phantom.APP_ERROR, message)
                summary["seed_containers_message"] = message

            action_result.update_summary(summary)

            return action_result.set_status(
                phantom.APP_SUCCESS, "Successfully loaded phantom demo data"
            )
        except Exception:
            return action_result.set_status(phantom.APP_ERROR, traceback.format_exc())

    def _handle_run_script(self, param):
        self.save_progress(
            "In action handler for: {0}".format(self.get_action_identifier())
        )
        action_result = self.add_action_result(ActionResult(dict(param)))
        summary = {}

        status, response = self._get_github_data()
        if not (status):
            return action_result.set_status(phantom.APP_ERROR, response)

        status, untar_response = self._save_github_data(response)
        if not (status):
            return action_result.set_status(phantom.APP_ERROR, response)

        scripts_file = glob.glob(
            "{0}/*/scripts/{1}".format(untar_response, param["name"])
        )
        if param.get("load_config"):
            config = self.get_config()
        else:
            config = None
        if config:
            _, automation_token = self.get_automation_key(username='automation')
            config['phantom_base_url'] = self.get_phantom_base_url_formatted()
            config['phantom_api_key'] = automation_token
            stdout, stderr = subprocess.Popen(
                [
                    "phenv",
                    "python",
                    scripts_file[0],
                    "--config",
                    "{}".format(json.dumps(config)),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            ).communicate()
        else:
            # args = json.loads(param.get("kwargs"))
            stderr = "Haven't coded else condition yet. Sorry :shrug:"
            # todo:
            # stdout, stderr = subprocess.Popen(['phenv', 'python3.6', param.get('kwargs')], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).communicate()

        if stderr:
            return action_result.set_status(
                phantom.APP_ERROR, "Unable to run script: - {}".format(stderr)
            )
        action_result.add_data({"stdout": stdout, "stderr": stderr})
        action_result.update_summary(summary)
        return action_result.set_status(phantom.APP_SUCCESS, "Succesfully ran script")

    def handle_action(self, param):
        ret_val = phantom.APP_SUCCESS

        # Get the action that we are supposed to execute for this App Run
        action_id = self.get_action_identifier()

        self.debug_print("action_id", self.get_action_identifier())

        if action_id == "test_connectivity":
            ret_val = self._handle_test_connectivity(param)

        elif action_id == "list_vms":
            ret_val = self._handle_list_vms(param)

        elif action_id == "update_object":
            ret_val = self._handle_update_object(param)

        elif action_id == "load_demo_data":
            ret_val = self._handle_load_demo_data(param)

        elif action_id == "run_script":
            ret_val = self._handle_run_script(param)

        return ret_val

    def initialize(self):

        config = self.get_config()
        self.soar_username = config["soar_username"]
        self.soar_password = config["soar_password"]
        self.github_repo_path = config["github_repo_path"]
        self.github_personal_access_token = config["github_personal_access_token"]
        self.github_branch = config["github_branch"]

        return phantom.APP_SUCCESS

    def finalize(self):
        return phantom.APP_SUCCESS


def main():
    import argparse

    import pudb

    pudb.set_trace()

    argparser = argparse.ArgumentParser()

    argparser.add_argument("input_test_json", help="Input Test JSON file")
    argparser.add_argument("-u", "--username", help="username", required=False)
    argparser.add_argument("-p", "--password", help="password", required=False)

    args = argparser.parse_args()
    session_id = None

    username = args.username
    password = args.password

    if username is not None and password is None:

        # User specified a username but not a password, so ask
        import getpass

        password = getpass.getpass("Password: ")

    if username and password:
        try:
            login_url = ZcatmanConnector._get_phantom_base_url_formatted() + "/login"

            print("Accessing the Login page")
            r = requests.get(login_url, verify=False)
            csrftoken = r.cookies["csrftoken"]

            data = dict()
            data["username"] = username
            data["password"] = password
            data["csrfmiddlewaretoken"] = csrftoken

            headers = dict()
            headers["Cookie"] = "csrftoken=" + csrftoken
            headers["Referer"] = login_url

            print("Logging into Platform to get the session id")
            r2 = requests.post(login_url, verify=False, data=data, headers=headers)
            session_id = r2.cookies["sessionid"]
        except Exception as e:
            print("Unable to get session id from the platform. Error: " + str(e))
            exit(1)

    with open(args.input_test_json) as f:
        in_json = f.read()
        in_json = json.loads(in_json)
        print(json.dumps(in_json, indent=4))

        connector = ZcatmanConnector()
        connector.print_progress_message = True

        if session_id is not None:
            in_json["user_session_token"] = session_id
            connector._set_csrf_info(csrftoken, headers["Referer"])

        ret_val = connector._handle_action(json.dumps(in_json), None)
        print(json.dumps(json.loads(ret_val), indent=4))

    exit(0)


if __name__ == "__main__":
    main()
