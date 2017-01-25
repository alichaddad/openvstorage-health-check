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
# but WITHOUT ANY WARRANTY of any kind.
import os
import glob
import psutil
import socket
import subprocess
from ovs.extensions.generic.configuration import Configuration, NotFoundException
from ovs.extensions.generic.sshclient import SSHClient
from ovs.extensions.generic.system import System
from ovs.extensions.healthcheck.decorators import expose_to_cli
from ovs.extensions.healthcheck.helpers.filesystem import FilesystemHelper
from ovs.extensions.healthcheck.helpers.helper import Helper
from ovs.extensions.healthcheck.helpers.network import NetworkHelper
from ovs.extensions.healthcheck.helpers.rabbitmq import RabbitMQ
from ovs.extensions.healthcheck.helpers.vpool import VPoolHelper
from ovs.extensions.packages.package import PackageManager
from ovs.extensions.services.service import ServiceManager
from ovs.lib.storagerouter import StorageRouterController
from timeout_decorator import timeout
from timeout_decorator.timeout_decorator import TimeoutError
from volumedriver.storagerouter import storagerouterclient as src
from volumedriver.storagerouter.storagerouterclient import ClusterNotReachableException


class OpenvStorageHealthCheck(object):
    """
    A healthcheck for the Open vStorage framework
    """
    MODULE = 'ovs'
    LOCAL_SR = System.get_my_storagerouter()
    LOCAL_ID = System.get_my_machine_id()

    CELERY_CHECK_TIME = 7

    @staticmethod
    @expose_to_cli(MODULE, 'local-settings-test')
    def get_local_settings(result_handler):
        """
        Fetch settings of the local Open vStorage node
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: local settings of the node
        :rtype: dict
        """
        test_name = '{0}-local-settings-test'.format(OpenvStorageHealthCheck.MODULE)
        ovs_version = Helper.get_ovs_version()
        result_handler.info('Fetching LOCAL information of node: ')
        # Fetch all details
        local_settings = {}
        try:
            local_settings = {'cluster_id' : Helper.get_cluster_id(),
                              'hostname': socket.gethostname(),
                              'storagerouter_id': OpenvStorageHealthCheck.LOCAL_ID,
                              'storagerouter_type': OpenvStorageHealthCheck.LOCAL_SR.node_type,
                              'environment_release': ovs_version[0],
                              'environment_branch': ovs_version[1].title(),
                              'environment os': Helper.check_os()}
        except (subprocess.CalledProcessError, NotFoundException, IOError) as ex:
            result_handler.failure('Could not fetch local-settings. Got {0}'.format(ex.message), test_name)
        else:
            for key, value in local_settings.iteritems():
                result_handler.info('{0}: {1}'.format(key.replace('_', ' ').title(), value))
            result_handler.success('Fetched all local settings', test_name)
        return local_settings

    @staticmethod
    @expose_to_cli(MODULE, 'log-files-test')
    def check_size_of_log_files(result_handler, max_log_size=Helper.max_log_size):
        """
        Checks the size of the initialized log files
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :param max_log_size: maximum log size of a log file (in MB)
        :type max_log_size: double
        :return: None
        :rtype: NoneType
        """
        def get_log_files_by_path(start_path, recursive=True):
            files_to_check = []
            for entry in os.listdir(start_path):
                entry_path = '{0}/{1}'.format(start_path, entry)
                if os.path.isdir(entry_path) and recursive is True:
                    files_to_check.extend(get_log_files_by_path(entry_path))
                elif entry.endswith('.log'):
                    files_to_check.append(entry_path)
            return files_to_check

        test_name = '{0}-log-files-test'.format(OpenvStorageHealthCheck.MODULE)
        good_size = []
        too_big = []
        result_handler.info('Checking if log files their size is not bigger than {0} MB: '.format(max_log_size))

        for c_files in get_log_files_by_path('/var/log/'):
            # check if logfile is larger than max_size
            if os.stat(c_files).st_size < 1024 ** 2 * max_log_size:
                good_size.append(c_files)
                result_handler.success('Logfile {0} size is fine!'.format(c_files), test_name)
            else:
                too_big.append(c_files)
                result_handler.warning('Logfile {0} is larger than {0} MB!'.format(c_files, max_log_size), test_name)

        if len(too_big) != 0:
            result_handler.failure('The following log files are too big: {0}.'.format(', '.join(too_big)), test_name)
        else:
            result_handler.success('All log files are ok!', test_name)

    @staticmethod
    @expose_to_cli('ovs', 'nginx-ports-test')
    def check_nginx_ports(result_handler):
        """
        Checks the extra ports from ovs
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        return OpenvStorageHealthCheck._check_extra_ports(result_handler, 'nginx', '{0}-nginx-ports-test'.format(OpenvStorageHealthCheck.MODULE))

    @staticmethod
    @expose_to_cli(MODULE, 'memcached-ports-test')
    def check_memcached_ports(result_handler):
        """
        Checks the extra ports from ovs
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        return OpenvStorageHealthCheck._check_extra_ports(result_handler, 'memcached', '{0}-memcached-ports-test'.format(OpenvStorageHealthCheck.MODULE))

    @staticmethod
    def _check_extra_ports(result_handler, key, test_name):
        """
        Checks the extra ports for key specified in the settings.json
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :param key: check all ports for this key
        :type key: string
        :return: None
        :rtype: NoneType
        """
        result_handler.info('Checking {0} ports'.format(key))
        ip = OpenvStorageHealthCheck.LOCAL_SR.ip
        if key not in Helper.extra_ports:
            raise RuntimeError('Settings.json is incorrect! The extra ports to check do not have {0}'.format(key))
        for port in Helper.extra_ports[key]:
            result_handler.info('Checking port {0} of service {1}.'.format(port, key))
            result = NetworkHelper.check_port_connection(port, ip)
            if result:
                result_handler.success('Connection successfully established to service {0} on {1}:{2}'.format(key, ip, port), test_name)
            else:
                result_handler.failure('Connection FAILED to service {0} on {1}:{2}'.format(key, ip, port), test_name)

    @staticmethod
    @expose_to_cli(MODULE, 'celery-ports-test')
    def check_rabbitmq_ports(result_handler):
        """
        Checks all ports of Open vStorage components rabbitMQ and celery
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-celery-ports-test'.format(OpenvStorageHealthCheck.MODULE)
        # Check Celery and RabbitMQ
        if Helper.get_ovs_type() != 'MASTER':
            result_handler.skip('RabbitMQ is not running/active on this server!', 'port_celery')
        result_handler.info('Checking Celery.')
        from errno import errorcode
        try:
            # noinspection PyUnresolvedReferences
            from celery.task.control import inspect
            stats = inspect().stats()
            if stats:
                result_handler.success('Successfully connected to Celery on all nodes.', test_name)
            else:
                result_handler.failure('No running Celery workers were found.', test_name)
        except IOError as ex:
            msg = 'Could not connect to Celery. Got {0}.'.format(ex)
            if len(ex.args) > 0 and errorcode.get(ex.args[0]) == 'ECONNREFUSED':
                msg += ' Check that the RabbitMQ server is running.'
                result_handler.failure(msg, test_name)
        except ImportError as ex:
            result_handler.failure('Could not import the celery module. Got {}'.format(str(ex)), test_name)

    @staticmethod
    @expose_to_cli(MODULE, 'packages-test')
    def check_ovs_packages(result_handler):
        """
        Checks the availability of packages for Open vStorage
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-packages_test'.format(OpenvStorageHealthCheck.MODULE)
        result_handler.info('Checking OVS packages: ', 'check_ovs_packages')
        client = SSHClient(OpenvStorageHealthCheck.LOCAL_SR)
        # PackageManager.SDM_PACKAGE_NAMES for sdm
        required_packages = list(PackageManager.OVS_PACKAGE_NAMES)
        extra_packages = list(Helper.packages)
        installed = PackageManager.get_installed_versions(client=client, package_names=list(required_packages + extra_packages))
        while len(required_packages) > 0:
            package = required_packages.pop()
            version = installed.get(package, '')
            if version:
                result_handler.success(
                    'Package {0} is installed with version {1}'.format(package, version.replace('\n', '')), test_name)
            else:
                result_handler.failure('Package {0} is not installed.'.format(package), test_name)
        while len(extra_packages) > 0:
            package = extra_packages.pop()
            version = installed.get(package, '')
            if version:
                result_handler.success(
                    'Package {0} is installed with version {1}'.format(package, version.replace('\n', '')), test_name)
            else:
                result_handler.skip('Package {0} is not installed.'.format(package), test_name)

    @staticmethod
    @expose_to_cli(MODULE, 'processes-test')
    def check_ovs_processes(logger):
        """
        Checks the availability of processes for Open vStorage
        :param logger: logging object
        :type logger: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-processes-test'.format(OpenvStorageHealthCheck.MODULE)
        logger.info('Checking local ovs services.')
        client = SSHClient(OpenvStorageHealthCheck.LOCAL_SR)
        services = [service for service in ServiceManager.list_services(client=client) if service.startswith(OpenvStorageHealthCheck.MODULE)]
        if len(services) == 0:
            logger.warning('Found no local ovs services.', test_name)
        for service_name in services:
            if ServiceManager.get_service_status(service_name, client)[0] is True:
                logger.success('Service {0} is running!'.format(service_name), test_name)
            else:
                logger.failure('Service {0} is not running, please check this.'.format(service_name), test_name)

    @staticmethod
    @timeout(CELERY_CHECK_TIME)
    def _check_celery():
        """
        Preliminary/Simple check for Celery and RabbitMQ component
        """
        # try if celery works smoothly
        try:
            guid = OpenvStorageHealthCheck.LOCAL_SR.guid
            machine_id = OpenvStorageHealthCheck.LOCAL_SR.machine_id
            obj = StorageRouterController.get_support_info.s(guid).apply_async(routing_key='sr.{0}'.format(machine_id)).get()
        except TimeoutError as ex:
            raise TimeoutError('{0}: Process is taking to long!'.format(ex.value))
        if obj:
            return True
        else:
            return False

    @staticmethod
    @expose_to_cli(MODULE, 'workers-test')
    def check_ovs_workers(result_handler):
        """
        Extended check of the Open vStorage workers; When the simple check fails, it will execute a full/deep check.

        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-workers-test'.format(OpenvStorageHealthCheck.MODULE)
        result_handler.info('Checking if OVS-WORKERS are running smoothly: ')

        # checking celery
        try:
            # basic celery check
            OpenvStorageHealthCheck._check_celery()
            result_handler.success('The OVS-WORKERS are working smoothly!', test_name)
        except TimeoutError:
            # apparently the basic check failed, so we are going crazy
            result_handler.failure('The test timed out after {0}s! Is RabbitMQ and ovs-workers running?'.format(OpenvStorageHealthCheck.CELERY_CHECK_TIME),
                                   test_name)
        except Exception as ex:
            result_handler.failure('The celery check has failed with {0}'.format(str(ex)), test_name)
            return False

    @staticmethod
    @expose_to_cli(MODULE, 'directories-test')
    def check_required_dirs(result_handler):
        """
        Checks the directories their rights and owners for mistakes

        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-directories-test'.format(OpenvStorageHealthCheck.MODULE)
        result_handler.info('Checking if OWNERS are set correctly on certain maps: ')
        for dirname, owner_settings in Helper.owners_files.iteritems():
            # check if directory/file exists
            if os.path.exists(dirname):
                if owner_settings.get('user') == FilesystemHelper.get_owner_of_file(dirname) \
                        and owner_settings.get('group') == FilesystemHelper.get_group_of_file(dirname):
                    result_handler.success('Directory {0} has correct owners!'.format(dirname), test_name)
                else:
                    result_handler.failure(
                        'Directory {0} has INCORRECT owners! It must be OWNED by USER={1} and GROUP={2}'
                        .format(dirname, owner_settings.get('user'), owner_settings.get('group')), test_name)
            else:
                result_handler.skip('Directory {0} does not exists!'.format(dirname), test_name)

        result_handler.info('Checking if Rights are set correctly on certain maps: ')
        for dirname, rights in Helper.rights_dirs.iteritems():
            # check if directory/file exists
            if os.path.exists(dirname):
                if FilesystemHelper.check_rights_of_file(dirname, rights):
                    result_handler.success('Directory {0} has correct rights!'.format(dirname), test_name)
                else:
                    result_handler.failure('Directory {0} has INCORRECT rights! It must be CHMOD={1} '.format(dirname, rights), test_name)
            else:
                result_handler.skip('Directory {0} does not exists!'.format(dirname), test_name)

    @staticmethod
    @expose_to_cli(MODULE, 'dns-test')
    def check_if_dns_resolves(result_handler, fqdn='google.com'):
        """
        Checks if DNS resolving works on a local machine

        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :param fqdn: the absolute pathname of the file
        :type fqdn: str
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-dns-test'.format(OpenvStorageHealthCheck.MODULE)
        result_handler.info('Checking DNS resolving: ')
        result = NetworkHelper.check_if_dns_resolves(fqdn)
        if result is True:
            result_handler.success('DNS resolving works!', test_name)
        else:
            result_handler.failure('DNS resolving doesnt work, please check /etc/resolv.conf or add correct DNS server and make it immutable: "sudo chattr +i /etc/resolv.conf"!',
                                   test_name)

    @staticmethod
    @expose_to_cli(MODULE, 'zombie-processes-test')
    def check_zombied_and_dead_processes(result_handler):
        """
        Finds zombie or dead processes on a local machine
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-zombies-processes-test'.format(OpenvStorageHealthCheck.MODULE)

        zombie_processes = []
        dead_processes = []

        result_handler.info('Checking for zombie/dead processes: ')

        # check for zombie'd and dead processes
        for proc in psutil.process_iter():
            try:
                pinfo = proc.as_dict(attrs=['pid', 'name', 'status'])
            except psutil.NoSuchProcess:
                pass
            else:
                if pinfo.get('status') == psutil.STATUS_ZOMBIE:
                    zombie_processes.append('{0}({1})'.format(pinfo.get('name'), pinfo.get('pid')))

                if pinfo.get('status') == psutil.STATUS_DEAD:
                    dead_processes.append('{0}({1})'.format(pinfo.get('name'), pinfo.get('pid')))

        # check if there zombie processes
        if len(zombie_processes) == 0:
            result_handler.success('There are no zombie processes on this node!', test_name)
        else:
            result_handler.warning('We DETECTED zombie processes on this node: {0}'.format(', '.join(zombie_processes)), test_name)

        # check if there dead processes
        if len(dead_processes) == 0:
            result_handler.success('There are no dead processes on this node!', 'process_dead')
        else:
            result_handler.failure('We DETECTED dead processes on this node: {0}'.format(', '.join(dead_processes)), test_name)

    @staticmethod
    @expose_to_cli(MODULE, 'model-test')
    def check_model_consistency(result_handler):
        """
        Checks if the model consistency of OVSDB vs. VOLUMEDRIVER and does a preliminary check on RABBITMQ
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-model-test'.format(OpenvStorageHealthCheck.MODULE)
        result_handler.info('Checking model consistency: ')

        # Checking consistency of volumedriver vs. ovsdb and backwards
        for vp in VPoolHelper.get_vpools():
            if vp.guid not in OpenvStorageHealthCheck.LOCAL_SR.vpools_guids:
                result_handler.skip('Skipping vPool {0} because it is not living here.'.format(vp.name), test_name)
                continue
            result_handler.info('Checking consistency of volumedriver vs. ovsdb for {0}: '.format(vp.name))
            missing_in_volumedriver = []
            missing_in_model = []
            config_file = Configuration.get_configuration_path('/ovs/vpools/{0}/hosts/{1}/config'.format(vp.guid, vp.storagedrivers[0].name))
            try:
                voldrv_client = src.LocalStorageRouterClient(config_file)
                # noinspection PyArgumentList
                voldrv_volume_list = voldrv_client.list_volumes()
            except (ClusterNotReachableException, RuntimeError) as ex:
                result_handler.failure('Seems like the volumedriver {0} is not running. Got {1}'.format(vp.name, ex.message), test_name)
                continue

            vdisk_volume_ids = []
            # cross-reference model vs. volumedriver
            for vdisk in vp.vdisks:
                vdisk_volume_ids.append(vdisk.volume_id)
                if vdisk.volume_id not in voldrv_volume_list:
                    missing_in_volumedriver.append(vdisk.guid)
                else:
                    voldrv_volume_list.remove(vdisk.volume_id)
            # cross-reference volumedriver vs. model
            for voldrv_id in voldrv_volume_list:
                if voldrv_id not in vdisk_volume_ids:
                    missing_in_model.append(voldrv_id)

            # display discrepancies for vPool
            if len(missing_in_volumedriver) != 0:
                result_handler.warning('Detected volumes that are MISSING in volumedriver but are in ovsdb in vpool: {0} - vdisk guid(s):{1}.'
                                       .format(vp.name, ' '.join(missing_in_volumedriver)), test_name)
            else:
                result_handler.success('No discrepancies found for ovsdb in vPool {0}'.format(vp.name), test_name)

            if len(missing_in_model) != 0:
                result_handler.warning('Detected volumes that are AVAILABLE in volumedriver but are not in ovsdb in vpool: {0} - vdisk volume id(s):{1}'
                                       .format(vp.name, ', '.join(missing_in_model)), test_name)
            else:
                result_handler.success('No discrepancies found for voldrv in vpool {0}'.format(vp.name), test_name)

    @staticmethod
    @expose_to_cli(MODULE, 'verify-rabbitmq-test')
    def verify_rabbitmq(result_handler):
        """
        Verify rabbitmq
        :param result_handler: logging object
        :type result_handler: ovs.extensions.healthcheck.result.HCResults
        :return: None
        :rtype: NoneType
        """
        test_name = '{0}-verify-rabbitmq-test'.format(OpenvStorageHealthCheck.MODULE)
        # RabbitMQ check: cluster verification
        result_handler.info('Pre-check: verification of RabbitMQ cluster: ')
        if Helper.get_ovs_type() == 'MASTER':
            r = RabbitMQ(ip=OpenvStorageHealthCheck.LOCAL_SR.ip)
            partitions = r.partition_status()
            if len(partitions) == 0:
                result_handler.success('RabbitMQ has no partition issues!', test_name)
            else:
                result_handler.failure('RabbitMQ has partition issues: {0}'.format(', '.join(partitions)), test_name)
        else:
            result_handler.skip('RabbitMQ is not running/active on this server!', test_name)
