#!/usr/bin/python

# Copyright (C) 2016 iNuron NV
#
# This file is part of Open vStorage Open Source Edition (OSE),
# as available from
#
#      http://www.openvstorage.org and
#      http://www.openvstorage.com.
#
# This file is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
# as published by the Free Software Foundation, in version 3 as it comes
# in the LICENSE.txt file of the Open vStorage OSE distribution.
#
# Open vStorage is distributed in the hope that it will be useful,

"""
Title: Utils
Description: Utilities for OVS health check
"""

"""
Section: Import package(s)
"""

# general packages
import subprocess
import xmltodict
import datetime
import commands
import json
import os

# ovs packages
from ovs.extensions.services.service import ServiceManager
from ovs.extensions.generic.sshclient import SSHClient
from ovs.extensions.generic.system import System

"""
Section: Classes
"""


class _Colors:
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    SKIP = '\033[95m'
    ENDC = '\033[0m'


class Utils:
    def __init__(self, unattended_mode, silent_mode=False):
        # module specific
        self.module = "utils"

        # load config file
        PARENT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
        with open("{0}/conf/settings.json".format(PARENT)) as settings_file:
            self.settings = json.load(settings_file)

        # fetch from config file
        self.HEALTHCHECK_DIR = self.settings["healthcheck"]["logging"]["directory"]
        self.HEALTHCHECK_FILE = self.settings["healthcheck"]["logging"]["file"]
        self.debug = self.settings["healthcheck"]["debug_mode"]
        self.max_log_size = self.settings["healthcheck"]["max_check_log_size"]  # in MB

        # open ovs ssh client
        self.client = SSHClient('127.0.0.1', username='root')

        # init at runtime
        self.etcd = self.detectEtcd()
        self.serviceManager = self.detectServiceManager()
        self.node_type = self.detectOvsType()
        self.ovs_version = self.detectOvsVersion()
        self.cluster_id = self.getClusterId()

        # utils log settings
        if silent_mode:
            # if silent_mode is true, the unattended is also true
            self.unattended_mode = True
            self.silent_mode = True
        else:
            self.unattended_mode = unattended_mode
            self.silent_mode = False

        # HC counters
        self.failure = 0
        self.success = 0
        self.warning = 0
        self.info = 0
        self.exception = 0
        self.skip = 0
        self.debug = 0

        # result of healthcheck in dict form
        self.healthcheck_dict = {}

        # create if dir does not exists
        if not os.path.isdir(self.HEALTHCHECK_DIR):
            os.makedirs(self.HEALTHCHECK_DIR)

    def fetchConfigFilePath(self, name, node_id, product, guid=None):

        # INFO
        # guid is only for volumedriver (vpool) config and proxy configs

        # fetch config file through etcd or local

        # product_name:
        #
        # arakoon = 0
        # vpool = 1
        # alba_backends = 2
        # alba_asds = 3
        # ovs = 4

        if not self.etcd:
            if product == 0:
                return "/opt/OpenvStorage/config/arakoon/{0}/{0}.cfg".format(name)
            elif product == 1:
                return "/opt/OpenvStorage/config/storagedriver/storagedriver/{0}.json".format(name)
            elif product == 4:
                return "/opt/OpenvStorage/config/ovs.json"
        else:
            if product == 0:
                return "etcd://127.0.0.1:2379/ovs/arakoon/{0}/config".format(name)
            elif product == 1:
                if not guid and self.etcd:
                    raise Exception("You must provide a 'vPOOL_guid' for ETCD, currently this is 'None'")
                else:
                    return "etcd://127.0.0.1:2379/ovs/vpools/{0}/hosts/{1}/config".format(guid, name+node_id)
            elif product == 4:
                return "etcd://127.0.0.1:2379/ovs/framework"


    def detectOvsType(self):
        return System.get_my_storagerouter().node_type

    def detectOvsVersion(self):
        with open("/opt/OpenvStorage/webapps/frontend/locales/en-US/ovs.json") as ovs_json:
            ovs = json.load(ovs_json)

        return ovs["releasename"]

    def getClusterId(self):

        if self.etcd:
            return self.getEtcdInformation("/ovs/framework/cluster_id")[0].translate(None, '\"')
        else:
            with open("/opt/OpenvStorage/config/ovs.json") as ovs_json:
                ovs = json.load(ovs_json)

            return ovs["support"]["cid"]

    def detectEtcd(self):
        result = self.executeBashCommand("dpkg -l | grep etcd")

        if result[0] == '':
            return False
        else:
            return True

    def getEtcdInformation(self, location):
        return self.executeBashCommand("etcdctl get {0}".format(location))

    def parseXMLtoJSON(self, xml):
        # dumps converts to general json, loads converts to python value
        return json.loads(json.dumps(xmltodict.parse(str(xml))))

    def getStatusOfService(self, service_name):
        return ServiceManager.get_service_status(str(service_name), self.client)

    def executeBashCommand(self, cmd, subpro=False):
        if not subpro:
            return commands.getstatusoutput(str(cmd))[1].split('\n')
        else:
            return subprocess.check_output(str(cmd), stderr=subprocess.STDOUT, shell=True)

    def detectServiceManager(self):

        # service_types:
        #
        # init = 1
        # systemd = 0
        # other(s) (not supported) = -1

        # detects what service manager your system has
        DETSYS = "pidof systemd && echo 'systemd' || pidof /sbin/init && echo 'sysvinit' || echo 'other'"
        OUTPUT = commands.getoutput(DETSYS)

        # process output
        if 'systemd' in OUTPUT:
            return 0
        elif 'sysvinit':
            return 1
        else:
            raise Exception("Unsupported Service Manager detected, please contact support or file a bug @github")

    def logger(self, message, module, log_type, unattended_mode_name, unattended_print_mode=True):

        # unattended_print_mode & unattended_mode_name are required together
        #
        # log_types:
        #
        # failure = 0
        # success = 1
        # warning = 2
        # info = 3
        # exception = 4
        # skip = 5
        # debug = 6

        try:
            target = open('{0}/{1}'.format(self.HEALTHCHECK_DIR, self.HEALTHCHECK_FILE), 'a')
            now = datetime.datetime.now()

            if log_type == 0:
                target.write("{0} - [FAILURE] - [{1}] - {2}\n".format(now, module, message))
                self.failure += 1

                if not self.silent_mode:
                    if self.unattended_mode:
                        if unattended_print_mode:
                            print "{0} FAILURE".format(unattended_mode_name)
                            self.healthcheck_dict[unattended_mode_name] = "FAILURE"
                    else:
                        print _Colors.FAIL + "[FAILURE] " + _Colors.ENDC + "%s" % (str(message))
                else:
                    if unattended_print_mode:
                        self.healthcheck_dict[unattended_mode_name] = "FAILURE"

            elif log_type == 1:
                target.write("{0} - [SUCCESS] - [{1}] - {2}\n".format(now, module, message))
                self.success += 1

                if not self.silent_mode:
                    if self.unattended_mode:
                        if unattended_print_mode:
                            print "{0} SUCCESS".format(unattended_mode_name)
                            self.healthcheck_dict[unattended_mode_name] = "SUCCESS"
                    else:
                        print _Colors.OKGREEN + "[SUCCESS] " + _Colors.ENDC + "%s" % (str(message))
                else:
                    if unattended_print_mode:
                        self.healthcheck_dict[unattended_mode_name] = "SUCCESS"

            elif log_type == 2:
                target.write("{0} - [WARNING] - [{1}] - {2}\n".format(now, module, message))
                self.warning += 1

                if not self.silent_mode:
                    if self.unattended_mode:
                        if unattended_print_mode:
                            print "{0} WARNING".format(unattended_mode_name)
                            self.healthcheck_dict[unattended_mode_name] = "WARNING"
                    else:
                        print _Colors.WARNING + "[WARNING] " + _Colors.ENDC + "%s" % (str(message))
                else:
                    if unattended_print_mode:
                        self.healthcheck_dict[unattended_mode_name] = "WARNING"

            elif log_type == 3:
                target.write("{0} - [INFO] - [{1}] - {2}\n".format(now, module, message))
                self.info += 1

                # info_mode is NOT logged silently
                if not self.silent_mode:
                    if self.unattended_mode:
                        if unattended_print_mode:
                            print "{0} INFO".format(unattended_mode_name)
                            self.healthcheck_dict[unattended_mode_name] = "INFO"
                    else:
                        print _Colors.OKBLUE + "[INFO] " + _Colors.ENDC + "%s" % (str(message))

            elif log_type == 4:
                target.write("{0} - [EXCEPTION] - [{1}] - {2}\n".format(now, module, message))
                self.exception += 1

                if not self.silent_mode:
                    if self.unattended_mode:
                        if unattended_print_mode:
                            print "{0} EXCEPTION".format(unattended_mode_name)
                            self.healthcheck_dict[unattended_mode_name] = "EXCEPTION"
                    else:
                        print _Colors.FAIL + "[EXCEPTION] " + _Colors.ENDC + "%s" % (str(message))
                else:
                    if unattended_print_mode:
                        self.healthcheck_dict[unattended_mode_name] = "EXCEPTION"

            elif log_type == 5:
                target.write("{0} - [SKIPPED] - [{1}] - {2}\n".format(now, module, message))
                self.skip += 1

                if not self.silent_mode:
                    if self.unattended_mode:
                        if unattended_print_mode:
                            print "{0} SKIPPED".format(unattended_mode_name)
                            self.healthcheck_dict[unattended_mode_name] = "SKIPPED"
                    else:
                        print _Colors.SKIP + "[SKIPPED] " + _Colors.ENDC + "%s" % (str(message))
                else:
                    if unattended_print_mode:
                        self.healthcheck_dict[unattended_mode_name] = "SKIPPED"

            elif log_type == 6:
                if self.debug:
                    target.write("{0} - [DEBUG] - [{1}] - {2}\n".format(now, module, message))
                    self.debug += 1
                    print _Colors.OKBLUE + "[DEBUG] " + _Colors.ENDC + "%s" % (str(message))
                    self.healthcheck_dict[unattended_mode_name] = "DEBUG"

            else:
                target.write("{0} - [UNEXPECTED_EXCEPTION] - [{1}] - {2}\n".format(now, module, message))
                self.exception += 1

                if not self.silent_mode:
                    if self.unattended_mode:
                        if unattended_print_mode:
                            print "{0} UNEXPECTED_EXCEPTION".format(unattended_mode_name)
                            self.healthcheck_dict[unattended_mode_name] = "UNEXPECTED_EXCEPTION"
                    else:
                        print _Colors.FAIL + "[UNEXPECTED_EXCEPTION] " + _Colors.ENDC + "%s" % (str(message))
                else:
                    if unattended_print_mode:
                        self.healthcheck_dict[unattended_mode_name] = "UNEXPECTED_EXCEPTION"

            target.close()

        except Exception, e:
            print "An unexpected exception occured during logging in '{0}': \n{1}".format(self.HEALTHCHECK_DIR, e)
