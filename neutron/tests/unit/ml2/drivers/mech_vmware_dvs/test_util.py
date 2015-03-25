# Copyright 2010-2011 OpenStack Foundation
# Copyright 2012-2013 IBM Corp.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import string

import mock
from oslo_vmware import exceptions as vmware_exceptions
from oslo_vmware import vim_util

from neutron.plugins.ml2.drivers.mech_vmware_dvs import config
from neutron.plugins.ml2.drivers.mech_vmware_dvs import exceptions
from neutron.plugins.ml2.drivers.mech_vmware_dvs import util
from neutron.tests import base


CONF = config.CONF

fake_network = {'id': '34e33a31-516a-439f-a186-96ac85155a8c',
                'name': '_fake_network_',
                'admin_state_up': True}
fake_segment = {'segmentation_id': '102'}


class DVSControllerBaseTestCase(base.BaseTestCase):
    """Base of all DVSController tests"""

    def setUp(self):
        super(DVSControllerBaseTestCase, self).setUp()
        self.dvs_name = 'dvs_name'
        self.vim = mock.Mock()
        self.connection = self._get_connection_mock(self.dvs_name)
        self.controller = util.DVSController(self.dvs_name,
                                             self.connection)

    def _get_connection_mock(self, dvs_name):
        return NotImplementedError()


class DVSControllerTestCase(DVSControllerBaseTestCase):
    """Tests of DVSController that don't call API methods"""

    def test_creation(self):
        self.assertEqual(self.dvs_name, self.controller.dvs_name)
        self.assertIs(self.connection, self.controller.connection)

    def test__get_net_name(self):
        expect = fake_network['name'] + '-' + fake_network['id']
        self.assertEqual(expect, self.controller._get_net_name(fake_network))

    def test__get_net_name_without_name(self):
        net = fake_network.copy()
        net.pop('name')
        self.assertEqual(net['id'], self.controller._get_net_name(net))

    def test__get_net_name_illegal_characters(self):
        illegal_chars = {chr(code) for code in range(128)}
        illegal_chars -= set(string.letters)
        illegal_chars -= set(string.digits)
        illegal_chars.discard('-')
        illegal_chars.discard('_')

        for char in illegal_chars:
            net = fake_network.copy()
            net['name'] = char
            self.assertRaises(
                exceptions.InvalidNetworkName,
                self.controller._get_net_name, net)

    def test__get_net_name_too_long(self):
        net = fake_network.copy()
        max_len = max(0, 80 - len(net['id']) - 1)
        net['name'] = 'x' * max_len
        try:
            self.controller._get_net_name(net)
        except exceptions.InvalidNetworkName:
            self.fail((
                'Invalid maximum name limit. %d chars should still be '
                'allowed') % max_len)

        net['name'] += 'A'
        self.assertRaises(
            exceptions.InvalidNetworkName, self.controller._get_net_name, net)

    def _get_connection_mock(self, dvs_name):
        return mock.sentinel.connection


class DVSControllerNetworkCreationTestCase(DVSControllerBaseTestCase):
    def test_create_network(self):
        try:
            self.controller.create_network(fake_network, fake_segment)
        except AssertionError:
            raise
        except Exception as e:
            self.fail("Can't create network. Reason: %s" % e)
        else:
            self.assertEqual(6, self.connection.invoke_api.call_count)
            self.assertEqual(1, self.connection.wait_for_task.call_count)

    def test_create_network_which_is_blocked(self):
        org_side_effect = self.connection.invoke_api.side_effect

        def side_effect(module, method, *args, **kwargs):
            if method == 'CreateDVPortgroup_Task':
                blocked_spec = kwargs['spec'].defaultPortConfig.blocked
                self.assertEqual('0', blocked_spec.inherited)
                self.assertEqual('true', blocked_spec.value)
                return kwargs['spec']
            else:
                return org_side_effect(module, method, *args, **kwargs)

        self.connection.invoke_api.side_effect = side_effect
        network = dict(fake_network)
        network['admin_state_up'] = False
        self.controller.create_network(network, fake_segment)

    def test_create_network_raises_DVSNotFoundException(self):
        org_side_effect = self.connection.invoke_api.side_effect
        vim = self.vim

        def side_effect(module, method, *args, **kwargs):
            if args == (vim, 'network_folder1', 'childEntity'):
                return mock.Mock(ManagedObjectReference=[])
            else:
                return org_side_effect(module, method, *args, **kwargs)

        self.connection.invoke_api.side_effect = side_effect
        self.assertRaises(exceptions.DVSNotFound,
                          self.controller.create_network,
                          fake_network,
                          fake_segment)

    def test_create_network_raises_VMWareDVSException(self):
        # first we count calls
        self.controller.create_network(fake_network, fake_segment)
        api_calls = self.connection.invoke_api.call_count

        # then we throw VimException for every api call
        for i in range(api_calls):
            connection = self._get_connection_mock(self.dvs_name)
            org_side_effect = self.connection.invoke_api.side_effect

            def side_effect(*args, **kwargs):
                if connection.invoke_api.call_count == i + 1:
                    msg = ('Failed test with args: %(args)s '
                           'and kwargs: %(kwargs)s' % {'args': args,
                                                       'kwargs': kwargs})
                    raise vmware_exceptions.VimException(msg)
                return org_side_effect(*args, **kwargs)

            connection.invoke_api.side_effect = side_effect
            controller = util.DVSController(self.dvs_name, connection)
            self.assertRaises(exceptions.VMWareDVSException,
                              controller.create_network, fake_network,
                              fake_segment)

    def _get_connection_mock(self, dvs_name):
        def create_side_effect(namespace):
            if namespace in ('ns0:DVPortgroupConfigSpec',
                             'ns0:VMwareDVSPortSetting',
                             'ns0:VmwareDistributedVirtualSwitchVlanIdSpec',
                             'ns0:BoolPolicy'):
                return mock.Mock(name=namespace)
            else:
                self.fail('Unexpected call. Namespace: %s' % namespace)

        vim = self.vim
        vim.client.factory.create.side_effect = create_side_effect
        controlled_dvs = mock.Mock(_type='VmwareDistributedVirtualSwitch',
                                   name='controlled_dvs')
        wrong_dvs = mock.Mock(_type='VmwareDistributedVirtualSwitch',
                              name='wrong_dvs')
        not_dvs = mock.Mock(_type='not_dvs', name='not_dvs')
        objects = [wrong_dvs, controlled_dvs, not_dvs]

        def invoke_api_side_effect(module, method, *args, **kwargs):
            if module is vim_util:
                if method == 'get_objects':
                    if args == (vim, 'Datacenter', 100, ['name']):
                        return mock.Mock(objects=[
                            mock.Mock(obj='datacenter1')
                        ])
                elif method == 'get_object_property':
                    if args == (vim, 'datacenter1', 'networkFolder'):
                        return 'network_folder1'
                    elif args == (vim, 'network_folder1', 'childEntity'):
                        return mock.Mock(ManagedObjectReference=objects)
                    elif args == (vim, wrong_dvs, 'name'):
                        return 'wrong_dvs'
                    elif args == (vim, controlled_dvs, 'name'):
                        return dvs_name
                    elif args == (vim, not_dvs, 'name'):
                        self.fail('Called with not dvs')
            elif module == vim:
                if method == 'CreateDVPortgroup_Task':
                    self.assertEqual((controlled_dvs,), args)
                    self.assert_create_specification(kwargs['spec'])
                    return kwargs['spec']
            self.fail('Unexpected call. Module: %(module)s; '
                      'method: %(method)s; args: %(args)s, '
                      'kwargs: %(kwargs)s' % {'module': module,
                                              'method': method,
                                              'args': args,
                                              'kwargs': kwargs})

        invoke_api = mock.Mock(side_effect=invoke_api_side_effect)
        connection = mock.Mock(invoke_api=invoke_api, vim=vim)
        return connection

    def assert_create_specification(self, spec):
        self.assertEqual(
            self.controller._get_net_name(fake_network), spec.name)
        self.assertEqual(util.DVS_PORTS_NUMBER, spec.numPorts)
        self.assertEqual('ephemeral', spec.type)
        self.assertEqual('Managed By Neutron', spec.description)
        vlan_spec = spec.defaultPortConfig.vlan
        self.assertEqual(fake_segment['segmentation_id'],
                         vlan_spec.vlanId)
        self.assertEqual('0', vlan_spec.inherited)
        blocked_spec = spec.defaultPortConfig.blocked
        self.assertEqual('1', blocked_spec.inherited)
        self.assertEqual('false', blocked_spec.value)


class DVSControllerNetworkUpdateTestCase(DVSControllerBaseTestCase):
    def test_update_network(self):
        try:
            self.controller.update_network(fake_network)
        except AssertionError:
            raise
        except Exception as e:
            self.fail("Didn't update network. Reason: %s" % e)
        else:
            self.assertEqual(6, self.connection.invoke_api.call_count)
            self.assertEqual(1, self.connection.wait_for_task.call_count)

    def test_update_network_change_admin_state_to_down(self):
        org_side_effect = self.connection.invoke_api.side_effect

        def side_effect(module, method, *args, **kwargs):
            if 'config' in args:
                config = mock.Mock()
                config.defaultPortConfig.blocked.value = False
                return config
            elif method == 'ReconfigureDVPortgroup_Task':
                blocked_spec = kwargs['spec'].defaultPortConfig.blocked
                self.assertEqual('0', blocked_spec.inherited)
                self.assertEqual('true', blocked_spec.value)
                return kwargs['spec']
            else:
                return org_side_effect(module, method, *args, **kwargs)

        self.connection.invoke_api.side_effect = side_effect
        network = dict(fake_network)
        network['admin_state_up'] = False
        self.controller.update_network(network)

    def test_update_network_when_there_is_no_admin_state_transition(self):
        org_side_effect = self.connection.invoke_api.side_effect
        for state in (True, False):
            def side_effect(module, method, *args, **kwargs):
                if 'config' in args:
                    config = mock.Mock()
                    config.defaultPortConfig.blocked.value = state
                    return config
                elif method == 'ReconfigureDVPortgroup_Task':
                    self.fail('Request is not required, because there is no '
                              'transition of admin state')
                else:
                    return org_side_effect(module, method, *args, **kwargs)

            self.connection.invoke_api.side_effect = side_effect
            network = dict(fake_network)
            network['admin_state_up'] = not state
            self.controller.update_network(network)

    def assert_update_specification(self, spec):
        self.assertEqual('config_version', spec.configVersion)
        blocked_spec = spec.defaultPortConfig.blocked
        self.assertEqual('1', blocked_spec.inherited)
        self.assertEqual('false', blocked_spec.value)

    def _get_connection_mock(self, dvs_name):
        def create_side_effect(namespace):
            if namespace in ('ns0:BoolPolicy',
                             'ns0:VMwareDVSPortSetting',
                             'ns0:DVPortgroupConfigSpec'):
                return mock.Mock(name=namespace)
            else:
                self.fail('Unexpected call. Namespace: %s' % namespace)

        vim = self.vim
        vim.client.factory.create.side_effect = create_side_effect

        wrong_pg = mock.Mock(_type='DistributedVirtualPortgroup',
                             name='wrong_pg')
        pg_to_update = mock.Mock(_type='DistributedVirtualPortgroup',
                                 name='pg_to_update')
        not_pg = mock.Mock(_type='not_pg', name='not_pg')
        objects = [wrong_pg, pg_to_update, not_pg]

        def invoke_api_side_effect(module, method, *args, **kwargs):
            if module is vim_util:
                if method == 'get_objects':
                    if args == (vim, 'Datacenter', 100, ['name']):
                        return mock.Mock(objects=[
                            mock.Mock(obj='datacenter1')])
                elif method == 'get_object_property':
                    if args == (vim, 'datacenter1', 'network'):
                        return mock.Mock(ManagedObjectReference=objects)
                    elif args == (vim, wrong_pg, 'name'):
                        return 'wrong_pg'
                    elif args == (vim, pg_to_update, 'name'):
                        return util.DVSController._get_net_name(fake_network)
                    elif args == (vim, not_pg, 'name'):
                        self.fail('Called with not pg')
                    elif args == (vim, pg_to_update, 'config'):
                        config = mock.Mock()
                        config.defaultPortConfig.blocked.value = True
                        config.configVersion = 'config_version'
                        return config
            elif module == vim:
                if method == 'ReconfigureDVPortgroup_Task':
                    self.assertEqual((pg_to_update, ), args)
                    self.assert_update_specification(kwargs['spec'])
                    return kwargs['spec']

            self.fail('Unexpected call. Module: %(module)s; '
                      'method: %(method)s; args: %(args)s, '
                      'kwargs: %(kwargs)s' % {'module': module,
                                              'method': method,
                                              'args': args,
                                              'kwargs': kwargs})

        invoke_api = mock.Mock(side_effect=invoke_api_side_effect)
        connection = mock.Mock(invoke_api=invoke_api, vim=vim)
        return connection


class DVSControllerNetworkDeletionTestCase(DVSControllerBaseTestCase):
    def test_delete_network(self):
        try:
            self.controller.delete_network(fake_network)
        except AssertionError:
            raise
        except Exception as e:
            self.fail("Didn't delete network. Reason: %s" % e)
        else:
            self.assertEqual(5, self.connection.invoke_api.call_count)
            self.assertEqual(1, self.connection.wait_for_task.call_count)

    def test_delete_network_raises_VMWareDVSException(self):
        # first we count calls
        self.controller.delete_network(fake_network)
        api_calls = self.connection.invoke_api.call_count

        # then we throw VimException for every api call
        for i in range(api_calls):
            connection = self._get_connection_mock(self.dvs_name)
            org_side_effect = self.connection.invoke_api.side_effect

            def side_effect(*args, **kwargs):
                if connection.invoke_api.call_count == i + 1:
                    msg = ('Failed test with args: %(args)s '
                           'and kwargs: %(kwargs)s' % {'args': args,
                                                       'kwargs': kwargs})
                    raise vmware_exceptions.VimException(msg)
                return org_side_effect(*args, **kwargs)

            connection.invoke_api.side_effect = side_effect
            controller = util.DVSController(self.dvs_name, connection)
            self.assertRaises(exceptions.VMWareDVSException,
                              controller.delete_network, fake_network)

    def test_delete_network_tries_to_delete_non_existing_port_group(self):
        org_side_effect = self.connection.invoke_api.side_effect
        vim = self.vim

        def side_effect(module, method, *args, **kwargs):
            if args == (vim, 'datacenter1', 'network'):
                return mock.Mock(ManagedObjectReference=[])
            else:
                return org_side_effect(module, method, *args, **kwargs)

        self.connection.invoke_api.side_effect = side_effect
        try:
            self.controller.delete_network(fake_network)
        except exceptions.PortGroupNotFound:
            self.fail('Deletion of non existing network should pass silent')

    def _get_connection_mock(self, dvs_name):
        vim = self.vim
        wrong_pg = mock.Mock(_type='DistributedVirtualPortgroup',
                             name='wrong_pg')
        pg_to_delete = mock.Mock(_type='DistributedVirtualPortgroup',
                                 name='pg_to_delete')
        not_pg = mock.Mock(_type='not_pg', name='not_pg')
        objects = [wrong_pg, pg_to_delete, not_pg]

        def invoke_api_side_effect(module, method, *args, **kwargs):
            if module is vim_util:
                if method == 'get_objects':
                    if args == (vim, 'Datacenter', 100, ['name']):
                        return mock.Mock(objects=[
                            mock.Mock(obj='datacenter1')])
                elif method == 'get_object_property':
                    if args == (vim, 'datacenter1', 'network'):
                        return mock.Mock(ManagedObjectReference=objects)
                    elif args == (vim, wrong_pg, 'name'):
                        return 'wrong_pg'
                    elif args == (vim, pg_to_delete, 'name'):
                        return util.DVSController._get_net_name(fake_network)
                    elif args == (vim, not_pg, 'name'):
                        self.fail('Called with not pg')
            elif module == vim:
                if method == 'Destroy_Task':
                    self.assertEqual((pg_to_delete, ), args)
                    return

            self.fail('Unexpected call. Module: %(module)s; '
                      'method: %(method)s; args: %(args)s, '
                      'kwargs: %(kwargs)s' % {'module': module,
                                              'method': method,
                                              'args': args,
                                              'kwargs': kwargs})

        invoke_api = mock.Mock(side_effect=invoke_api_side_effect)
        connection = mock.Mock(invoke_api=invoke_api, vim=vim)
        return connection


class UtilTestCase(base.BaseTestCase):
    """TestCase for functions in util module"""

    def setUp(self):
        super(UtilTestCase, self).setUp()
        patch = mock.patch('oslo_vmware.api.VMwareAPISession',
                           return_value='session')
        self.session_mock = patch.start()
        self.addCleanup(patch.stop)

    def test_empty_map_if_config_network_maps_is_empty(self):
        CONF.set_override('network_maps', [], 'ml2_vmware')
        self.assertDictEqual(
            {},
            util.create_network_map_from_config(CONF.ml2_vmware))

    def test_cretes_network_map_from_conf(self):
        network_map = ['physnet1:dvSwitch', 'physnet2:dvSwitch1']
        CONF.set_override(
            'network_maps', network_map, 'ml2_vmware')
        actual = util.create_network_map_from_config(CONF.ml2_vmware)

        self.assertEqual(len(network_map), len(actual))

        for net, dvs_name in [i.split(':') for i in network_map]:
            controller = actual[net]
            self.assertEqual(dvs_name, controller.dvs_name)
            self.assertEqual('session', controller.connection)

        vmware_conf = config.CONF.ml2_vmware
        self.session_mock.assert_called_once_with(
            vmware_conf.vsphere_hostname,
            vmware_conf.vsphere_login,
            vmware_conf.vsphere_password,
            vmware_conf.api_retry_count,
            vmware_conf.task_poll_interval)