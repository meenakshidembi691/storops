# coding=utf-8
# Copyright (c) 2015 EMC Corporation.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
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
from __future__ import unicode_literals

import logging
from threading import Lock

from retryz import retry

from storops.vnx.enums import VNXSPEnum, VNXPortType
import storops.vnx.resource.lun
from storops.vnx.resource.port import VNXHbaPort
from storops.vnx.resource import VNXCliResource, VNXCliResourceList
from storops import exception as ex

__author__ = 'Cedric Zhuang'

log = logging.getLogger(__name__)


class VNXStorageGroup(VNXCliResource):
    def __init__(self, name=None, cli=None):
        super(VNXStorageGroup, self).__init__()
        self._cli = cli
        self._name = name

        self._uid = ''
        self._hba_port_map = []
        self._conn = None
        self._hlu_lock = Lock()

    def _get_raw_resource(self):
        return self._cli.get_sg(name=self._name, poll=self.poll)

    @classmethod
    def get(cls, cli, name=None):
        if name is None:
            ret = VNXStorageGroupList(cli)
        else:
            ret = VNXStorageGroup(name, cli)
        return ret

    @classmethod
    def create(cls, name, cli):
        out = cli.create_sg(name)
        msg = 'failed to create storage group "{}".'.format(name)
        ex.raise_if_err(out, msg, default=ex.VNXCreateStorageGroupError)
        return VNXStorageGroup(name, cli)

    def remove(self):
        self._cli.remove_sg(self._get_name(), poll=self.poll)

    def has_hlu(self, hlu):
        return hlu in self.used_hlu_numbers

    def has_alu(self, lun):
        alu = storops.vnx.resource.lun.VNXLun.get_id(lun)
        return alu in self.used_alu_numbers

    def get_hlu(self, lun):
        alu = storops.vnx.resource.lun.VNXLun.get_id(lun)
        return self.get_alu_hlu_map().get(alu, None)

    def is_valid(self):
        return len(self.name) > 0 and len(self.uid) > 0

    @property
    def hba_port_map(self):
        if not self._hba_port_map:
            self.hba_port_map = self.hba_sp_pairs
        return self._hba_port_map

    @hba_port_map.setter
    def hba_port_map(self, hba_sp_pairs):
        def _process_cli_output(value):
            port = VNXHbaPort.from_storage_group_hba(value)
            hba = value.uid
            self._hba_port_map.append((hba, port))

        if hba_sp_pairs is not None:
            for item in hba_sp_pairs:
                _process_cli_output(item)

    @property
    def port_list(self):
        return tuple(set(map(lambda x: x[1], self.hba_port_map)))

    @property
    def initiator_uid_list(self):
        return tuple(set(map(lambda x: x.uid, self.hba_sp_pairs)))

    def get_initiator_uids(self, port_type=None):
        ret = []
        for hba, port in self.hba_port_map:
            if port_type is not None:
                if port.type == port_type:
                    ret.append(hba)
            else:
                ret.append(hba)
        return tuple(set(ret))

    def get_ports(self, initiator_uid):
        ret = []
        for hba, port in self.hba_port_map:
            if hba == initiator_uid:
                ret.append(port)
        return tuple(set(ret))

    _hlu_full = None
    _max_hlu = 255

    @classmethod
    def get_max_luns_per_sg(cls):
        return cls._max_hlu

    @classmethod
    def set_max_luns_per_sg(cls, value):
        log.info('Update max LUNs per storage group to: {}'
                 .format(value))
        cls._max_hlu = value
        cls._hlu_full = None

    @classmethod
    def _hlu_full_set(cls):
        if cls._hlu_full is None:
            cls._hlu_full = set(range(1, cls.get_max_luns_per_sg() + 1))
        return set(cls._hlu_full)

    def get_alu_hlu_map(self):
        if self.alu_hlu_map is None:
            self._update_property_cache('alu_hlu_map', {})
        return self.alu_hlu_map

    @property
    def used_hlu_numbers(self):
        return self.get_alu_hlu_map().values()

    @property
    def used_alu_numbers(self):
        return self.get_alu_hlu_map().keys()

    def _get_hlu_to_add(self, alu):
        ret = None
        with self._hlu_lock:
            remain = self._hlu_full_set() - set(self.used_hlu_numbers)
            if len(remain) == 0:
                raise ex.VNXNoHluAvailableError(
                    'no hlu number available for attach.')
            ret = remain.pop()
            self.get_alu_hlu_map()[alu] = ret
        return ret

    def _remove_alu(self, alu):
        ret = None
        with self._hlu_lock:
            if self.has_alu(alu):
                ret = self.get_alu_hlu_map().pop(alu)
        return ret

    class _HluOccupiedError(Exception):
        pass

    def attach_alu(self, lun, retry_limit=None):
        def _update():
            self.update()

        @retry(on_error=ex.VNXAluNumberInUseError, on_retry=_update,
               limit=retry_limit)
        def _do(alu_id):
            hlu = self._get_hlu_to_add(alu_id)
            out = self._cli.sg_add_hlu(self._get_name(), hlu, alu_id,
                                       poll=self.poll)
            ex.raise_if_err(out, default=ex.VNXAttachAluError)
            return hlu

        lun_clz = storops.vnx.resource.lun.VNXLun
        alu = lun_clz.get_id(lun)
        return _do(alu)

    def detach_alu(self, lun):
        alu = storops.vnx.resource.lun.VNXLun.get_id(lun)
        hlu = self.get_hlu(lun)
        if hlu is None:
            raise ex.VNXDetachAluNotFoundError(
                'specified lun {} is not attached.'.format(alu))
        out = self._cli.sg_remove_hlu(self._get_name(), hlu, poll=self.poll)
        msg = 'failed to detach hlu {}/alu {}.'.format(hlu, alu)
        ex.raise_if_err(out, msg, default=ex.VNXStorageGroupError, )
        self._remove_alu(alu)

    def connect_host(self, host):
        out = self._cli.sg_connect_host(self._get_name(), host, poll=self.poll)
        msg = 'failed to connect host {}.'.format(host)
        ex.raise_if_err(out, msg, default=ex.VNXStorageGroupError)

    def disconnect_host(self, host):
        out = self._cli.sg_disconnect_host(self._get_name(), host,
                                           poll=self.poll)
        msg = 'failed to disconnect host {}.'.format(host)
        ex.raise_if_err(out, msg, default=ex.VNXStorageGroupError)

    def connect_hba(self, port, hba_uid, host_name, host_ip=None):
        return self.set_path(port, hba_uid, host_name, host_ip)

    @property
    def uid(self):
        return self.wwn

    def set_path(self, port, hba_uid, host_name, host_ip=None):
        if hasattr(port, 'sp'):
            sp = port.sp
        else:
            raise ValueError('sp is not available from {}.'.format(port))

        if hasattr(port, 'port_id'):
            port_id = port.port_id
        else:
            raise ValueError('port id is not available from {}.'.format(port))

        vport_id = None
        if hasattr(port, 'virtual_port_id'):
            # FCoE do not need vport_id
            if hasattr(port, 'type'):
                port_type = port.type
                if port_type == VNXPortType.ISCSI:
                    vport_id = port.virtual_port_id

        out = self._cli.set_path(self._get_name(), hba_uid, sp, port_id,
                                 host_ip, host_name, vport_id=vport_id)
        ex.raise_if_err(out)


class VNXStorageGroupList(VNXCliResourceList):
    @classmethod
    def get_resource_class(cls):
        return VNXStorageGroup

    def __init__(self, cli=None):
        super(VNXStorageGroupList, self).__init__(cli)
        self._sg_map = {}

    def add_sg(self, sg):
        self._sg_map[sg.name] = sg

    def detach_alu(self, lun):
        for sg in self:
            if sg.has_alu(lun):
                sg.detach_alu(lun)

    def _get_raw_resource(self):
        return self._cli.get_sg(poll=self.poll)


class VNXStorageGroupHBA(VNXCliResource):
    @property
    def sp(self):
        return VNXSPEnum.parse(self.hba[1])

    @property
    def port_id(self):
        return int(self.hba[2])

    @property
    def uid(self):
        return self.hba[0]

    @property
    def vlan(self):
        sp_port = self.sp_port
        if sp_port is not None and 'v' in sp_port:
            ret = int(sp_port[sp_port.find('v') + 1:])
        else:
            ret = None
        return ret

    @property
    def port_type(self):
        ret = None
        if '.' in self.uid:
            ret = VNXPortType.ISCSI
        elif ':' in self.uid:
            ret = VNXPortType.FC
        return ret

    def property_names(self):
        ret = super(VNXStorageGroupHBA, self).property_names()
        return ret + ['uid', 'sp', 'port_id', 'vlan', 'port_type']


class VNXStorageGroupHBAList(VNXCliResourceList):
    @classmethod
    def get_resource_class(cls):
        return VNXStorageGroupHBA
