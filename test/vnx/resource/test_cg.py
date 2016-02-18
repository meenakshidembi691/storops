# coding=utf-8
from __future__ import unicode_literals

from unittest import TestCase

from hamcrest import assert_that, equal_to, has_item, raises

from test.vnx.cli_mock import patch_cli, t_cli
from vnxCliApi.exception import VNXConsistencyGroupError
from vnxCliApi.vnx.parsers import get_parser_config
from vnxCliApi.vnx.resource.cg import VNXConsistencyGroup
from vnxCliApi.vnx.resource.cg import VNXConsistencyGroupList
from vnxCliApi.vnx.resource.lun import VNXLun

__author__ = 'Cedric Zhuang'


class VNXConsistencyGroupListTest(TestCase):
    @patch_cli()
    def test_parse(self):
        assert_that(len(VNXConsistencyGroupList(t_cli())), equal_to(2))


class VNXConsistencyGroupTest(TestCase):
    @patch_cli()
    def test_list_consistency_group(self):
        assert_that(len(VNXConsistencyGroup.get(t_cli())), equal_to(2))

    @patch_cli()
    def test_properties(self):
        cg = VNXConsistencyGroup(name="test_cg", cli=t_cli())
        assert_that(cg.name, equal_to('test_cg'))
        assert_that(cg.lun_list, equal_to([1, 3]))
        assert_that(cg.state, equal_to('Ready'))
        assert_that(cg.existed, equal_to(True))

    def test_update(self):
        parser = get_parser_config('VNXConsistencyGroup')
        data = {
            parser.NAME.key: 'test cg name',
            parser.LUN_LIST.key: [1, 5, 7],
            parser.STATE.key: 'Offline'
        }

        cg = VNXConsistencyGroup()
        cg.update(data)

        self.assertEqual('test cg name', cg.name)
        self.assertEqual([1, 5, 7], cg.lun_list)
        self.assertEqual('Offline', cg.state)

    def test_parse(self):
        output = """
                Name:  test cg name
                Name:  another cg
                """
        cgs = VNXConsistencyGroup.parse_all(output)
        self.assertEqual(2, len(cgs))
        names = [cg.name for cg in cgs]
        assert_that(names, has_item('test cg name'))
        assert_that(names, has_item('another cg'))

    @patch_cli()
    def test_add_member(self):
        def f():
            cg = VNXConsistencyGroup('test_cg', t_cli())
            m1 = VNXLun(name='m1', cli=t_cli())
            m2 = VNXLun(name='m2', cli=t_cli())
            cg.add_member(m1, m2)

        assert_that(f, raises(VNXConsistencyGroupError, 'Cannot add members'))

    @patch_cli()
    def test_has_member(self):
        cg = VNXConsistencyGroup('test_cg', t_cli())
        lun = VNXLun(lun_id=1)
        assert_that(cg.has_member(lun), equal_to(True))
        assert_that(cg.has_member(7), equal_to(False))

    @patch_cli()
    def test_cg_no_poll(self):
        def f():
            cg = VNXConsistencyGroup(name="test_cg", cli=t_cli())
            with cg.with_no_poll():
                cg.add_member(1, 2, 3)

        assert_that(f, raises(VNXConsistencyGroupError, 'does not exist'))
